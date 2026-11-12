"""
AgentControl —— 框架级 Agent 控制与委派/递话/结单

- framework_delegate / framework_task_settle / framework_task_status：
  跨 Agent 委派原语——每个执行者任何时刻至多一件【在做】，其余都在【待办】（还没开工的、
  先搁下带进度的）；换活必须同时交代在做那件怎么结（做完了 / 先搁下 / 不做了），
  日常结单与撤单直接报短编号（"在做"、"待办1"……），不把内部任务号念给模型
- framework_prompt：递话原语——纯文本留言（给正在做的同一件事补充要求，或在聊天里回话），
  不派新任务、不进账本；与委派共享 Agent 侧文本接缝，记账分家
- provider="framework"（框架内置提供，非独立源；可见名单默认仅主播侧）
- pause / resume / shutdown / 状态查询等控制能力由 ``AgentControl`` 类本体承载，
  不进 LLM 工具面；控制面（DashboardServer）经 API 直调，并与工具面共用同一个
  ``DelegationCore`` 单例

LLM 工具面注册方式（framework provider 含 delegate/prompt/task_status/task_settle 四个 spec）：
```python
core = DelegationCore(manager, task_ledger, event_bus=event_bus)
provider = build_agent_control_provider(manager, task_ledger, event_bus=event_bus, delegation_core=core)
tool_registry.register_provider(provider)
```
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Dict, Iterable, List, Optional

from src.modules.agents.base import BaseAgent
from src.modules.agents.delegation_board import (
    BACKLOG_LABEL_PREFIX,
    BACKLOG_NO_KEY,
    BACKLOG_SEQ_KEY,
    DELIVERED_AT_KEY,
    DELIVERIES_KEY,
    HELD_KEY,
    IN_HAND_LABEL,
    QUEUED_AT_KEY,
    SHELVED_AT_KEY,
    SHELVED_PROGRESS_KEY,
    Board,
    BoardItem,
    find_item,
    read_board,
)
from src.modules.agents.delegation_text import compose_redelivery, mark_prompt, own_words_of
from src.modules.agents.game_tools import business_fail_result
from src.modules.agents.manager import AgentManager
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.agents import AgentPromptedPayload
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.events.payloads.tool_result import ToolResultPayload
from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.tasks import (
    IN_PROGRESS_TASK_STATES,
    REPLY_NOTE_AT_KEY,
    REPLY_NOTE_KEY,
    TERMINAL_TASK_STATES,
    TaskLedger,
    TaskRecord,
)
from src.modules.utils.time_utils import now_ms

logger = get_logger("AgentControl")

# 提供者标识统一来源（ToolSpec.provider / 注册名前缀；主播 Planner 据此识别委派工具面）
PROVIDER_NAME = "framework"

# 每个执行者的待办容量上限：排后面超出上限时拒收并附当前在做与待办清单
_DELEGATION_QUEUE_LIMIT = 5

# 在做委派看门狗：执行者手上那件【在做】既无在跑子目标、又超过该时长没有任何工具或任务活动、
# 且未交付时，带短编号提醒发起方定夺（做完了 / 接着催 / 先搁下或作废），框架不代撤单
_DELEGATION_WATCHDOG_TIMEOUT_MS = 300_000
# 低频定时巡检间隔：静默期没有任务事件到达时，仍按此周期检查在做单是否停滞
_DELEGATION_WATCHDOG_INTERVAL_MS = 30_000
# 同一张在做单最多提醒次数（每次提醒重置窗口）：防发起方始终不决断时无限打扰
_DELEGATION_WATCHDOG_MAX_ESCALATIONS = 2

# 拒收理由里不当"已知进展"回显的快照键：指令全文与原话另有一行；执行侧最近一句推理是未脱敏原文；
# 看板簿记键是受理核心自己写的顺序与时刻，不是执行进展
_NOT_PROGRESS_KEYS = frozenset(
    {
        "instruction",
        "own_words",
        REPLY_NOTE_KEY,
        REPLY_NOTE_AT_KEY,
        BACKLOG_NO_KEY,
        BACKLOG_SEQ_KEY,
        QUEUED_AT_KEY,
        DELIVERIES_KEY,
        DELIVERED_AT_KEY,
        SHELVED_AT_KEY,
        SHELVED_PROGRESS_KEY,
        HELD_KEY,
        "urgent",
    }
)

_VALID_WHEN_BUSY = frozenset({"queue", "replace"})
_VALID_OUTCOMES = frozenset({"done", "shelve", "drop"})


def _emit_prompted(event_bus: Any, *, target: str, content: str, source: str) -> None:
    """广播 agent.prompted（递话受理成功后调用；观测旁路，失败仅记日志）。"""
    if event_bus is None:
        return
    payload = AgentPromptedPayload(target=target, content=content, source=source)

    async def _send() -> None:
        try:
            await event_bus.emit(CoreEvents.AGENT_PROMPTED, payload, source="AgentControl")
        except Exception as exc:  # noqa: BLE001 - 观测旁路
            logger.warning(f"agent.prompted 广播失败（target={target}）: {exc}")

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("无运行循环，agent.prompted 跳过调度（同步上下文）")
        return
    loop.create_task(_send())


def _merge_progress(caller_note: str, executor_note: str) -> str:
    """合并发起方补充的进度与执行方收手回报的进度（同文不重复）。"""
    left = caller_note.strip()
    right = executor_note.strip()
    if left and right and left != right:
        return f"{left}（执行侧记录：{right}）"
    return left or right


# -------------------- 工具规格 --------------------

#: 委派工具的固定说明
_DELEGATE_DESCRIPTION = (
    "安排目标 Agent 做一件事（给目标与完成标准，不给步骤），或把【待办】里已有的一件提上来做。"
    "每个目标任何时刻至多一件【在做】，其余都在【待办】。目标手上已有【在做】时："
    "when_busy='queue'（默认）排进待办后面，轮到时自动送达，不用重复派；"
    "when_busy='replace' 停下手上那件换成这件——此时必须同时填 current 交代当前【在做】怎么结："
    "'done'（已经做完了结单）/ 'shelve'（先搁下放回待办最前，保留做到哪）/ 'drop'（不做了作废）。"
)

#: 递话工具的固定说明
_PROMPT_DESCRIPTION = (
    "向目标 Agent 递话：不派新任务、不改账面单子。分两种（kind）："
    "'requirement'（默认）只给当前【在做】的同一件事补充要求、限制或回答卡点，要做的事变了（完成标准变了）"
    "必须改用 framework_delegate 换单；'chat' 只是在游戏聊天里回一句话，不改手上的事。"
)

#: 任务状态工具规格
_TASK_STATUS_SPEC: ToolSpec = ToolSpec(
    name="task_status",
    description=(
        "查询委派任务或回执型工具任务的当前状态与快照：可填 item（短编号如 '在做'、'待办1'）或 task_id。"
        "查询执行方的工作文档（待办/笔记/近期上报）用各游戏 Agent 自己的读取工具。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "目标 Agent 注册名（用 item 查询且仅有一个可委派目标时可留空）"},
            "item": {"type": "string", "description": "看板短编号（'在做' 或 '待办1'、'待办2'……）"},
            "task_id": {"type": "string", "description": "受理回执返回的任务号（可选，与 item 二选一）"},
        },
    },
    kind="sync",
    provider=PROVIDER_NAME,
)

#: 任务结单/搁下/撤单工具规格（取代旧 task_cancel，支持短编号三选一）
_TASK_SETTLE_SPEC: ToolSpec = ToolSpec(
    name="task_settle",
    description=(
        "按短编号处置一件委派（不用内部任务号，直接填【我手头在游戏里做的事】或【待办】里的 '在做'、'待办1'……）。"
        "outcome 三选一：'done'=认定这件已经做完了结单（在做的会收手并自动送达下一件待办）；"
        "'shelve'=把【在做】先停下搁回待办最前（保留做到哪）；"
        "'drop'=这件不做了撤单作废（待办件直接移除，不影响正在做的另一件）。"
        "执行方自己在游戏里挂的子目标不归这里管。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "目标 Agent 注册名（仅有一个可委派目标时可留空）"},
            "item": {
                "type": "string",
                "description": "要处置哪一件：填短编号 '在做' 或 '待办1'、'待办2'……",
            },
            "outcome": {
                "type": "string",
                "enum": ["done", "shelve", "drop"],
                "description": "处置结果：'done'=做完了结单，'shelve'=先搁下回待办最前，'drop'=不做了作废",
            },
            "progress": {
                "type": "string",
                "description": "选填：outcome='shelve' 时记一句做到哪了，或结单备注",
            },
        },
        "required": ["item", "outcome"],
    },
    kind="sync",
    provider=PROVIDER_NAME,
)


# 任务号序号源（模块级共享：多入口同一序列，避免同毫秒撞号）
_task_id_seq = 0


def _generate_task_id() -> str:
    """生成委派任务号（deleg_{epoch_ms}_{seq}，全链路关联键）。"""
    global _task_id_seq
    _task_id_seq += 1
    return f"deleg_{now_ms()}_{_task_id_seq}"


# -------------------- 委派受理核心（工具面与控制面单例共享） --------------------


@dataclass(slots=True)
class _DelegationOutcome:
    """委派受理结果——受理核心的统一产出，失败形态由各入口适配器自行映射。"""

    ok: bool
    error: str = ""
    message: str = ""
    task_id: str = ""
    executor: str = ""
    item: str = ""
    own_words: str = ""
    queued: bool = False
    queue_position: int = 0


@dataclass(slots=True)
class _SettleOutcome:
    """结单/搁下/撤单结果——统一产出。"""

    ok: bool
    error: str = ""
    message: str = ""
    task_id: str = ""
    executor: str = ""
    item: str = ""
    outcome: str = ""
    status: str = ""


@dataclass(slots=True)
class _WatchdogEntry:
    """每个执行者手上那件【在做】的活动看门狗计时。"""

    task_id: str
    timer_base_ms: int
    escalations: int = 0


class DelegationCore:
    """委派受理与结单核心：任务账本唯一事实源，组合根单例供 LLM 工具面与运营控制面共用。

    不变量：
    1. 每个执行者任何时刻至多一件【在做】（账面 accepted / running / waiting_for_decision），
       其余未了结的委派都在【待办】（账面 queued：未开工或先搁下）。
    2. 换活必须结单：目标手上有【在做】且选 ``when_busy="replace"`` 时，按顺序让执行者收手 →
       送达新件 → 结掉或搁下旧件；新件被目标拒收时立即把旧件原样送回执行者（送回也失败则搁回待办最前）。
    3. 发起方认定完成记 ``closed_by_initiator``，与执行方主动交付的 ``succeeded`` 严格区分。
    4. 看门狗盯实际活动：执行者名下没有在跑的 provider 子目标，且连续超过阈值没有任何工具调用
       或任务状态活动、仍未交付时，带短编号【在做】递话提醒发起方定夺；兼备低频定时循环与事件触发。
    """

    def __init__(
        self,
        manager: AgentManager,
        task_ledger: Optional[TaskLedger],
        event_bus: Optional[Any] = None,
        clock: Optional[Callable[[], int]] = None,
        *,
        watchdog_timeout_ms: int = _DELEGATION_WATCHDOG_TIMEOUT_MS,
        watchdog_interval_ms: int = _DELEGATION_WATCHDOG_INTERVAL_MS,
    ) -> None:
        self._manager = manager
        self._task_ledger = task_ledger
        self._event_bus = event_bus
        self._clock = clock or now_ms
        self._watchdog_timeout_ms = watchdog_timeout_ms
        self._watchdog_interval_ms = watchdog_interval_ms
        # 每个执行者一把异步锁：收手、换单、结单、自动送达串行化，防并发双投
        self._locks: Dict[str, asyncio.Lock] = {}
        # 待办短编号计数器（每个执行者只增不减，本场内稳定）与队头/队尾序号游标
        self._backlog_nos: Dict[str, int] = {}
        self._tail_seq = 0
        self._head_seq = 0
        # 看门狗：executor → 当前在做单的计时条目，以及执行者最近一次工具/目标活动时刻
        self._watchdog: Dict[str, _WatchdogEntry] = {}
        self._last_activity_ms: Dict[str, int] = {}
        self._watchdog_task: Optional[asyncio.Task[None]] = None
        self._stopping = False
        self._subscribed = False
        if event_bus is not None and task_ledger is not None:
            event_bus.on(CoreEvents.TASK_CHANGED, self._on_task_changed, model_class=TaskChangedPayload)
            event_bus.on(CoreEvents.TOOL_RESULT_WILDCARD, self._on_tool_result, model_class=ToolResultPayload)
            self._subscribed = True

    # -------------------- 生命周期与后台定时巡检 --------------------

    def start(self) -> None:
        """启动看门狗低频定时检查循环（幂等；无账本或间隔 ≤0 时跳过）。"""
        if self._task_ledger is None or self._watchdog_interval_ms <= 0:
            return
        if self._watchdog_task is not None and not self._watchdog_task.done():
            return
        self._stopping = False
        self._watchdog_task = asyncio.create_task(self._watchdog_loop(), name="delegation-watchdog")

    async def stop(self) -> None:
        """停止看门狗定时循环并退订事件（幂等）。"""
        self._stopping = True
        task = self._watchdog_task
        if task is not None:
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("委派看门狗定时循环取消等待超时（5s），继续停机")
            self._watchdog_task = None
        if self._subscribed and self._event_bus is not None:
            for event_name, handler in (
                (CoreEvents.TASK_CHANGED, self._on_task_changed),
                (CoreEvents.TOOL_RESULT_WILDCARD, self._on_tool_result),
            ):
                try:
                    self._event_bus.off(event_name, handler)
                except Exception as exc:  # noqa: BLE001 - 退订失败不阻断停机
                    logger.debug(f"委派核心退订 {event_name} 忽略异常: {exc}")
            self._subscribed = False

    async def _watchdog_loop(self) -> None:
        """低频巡检循环：静默期没有任何任务事件时，仍按固定周期检查在做单是否无实际活动。"""
        try:
            while not self._stopping:
                await asyncio.sleep(self._watchdog_interval_ms / 1000)
                if self._stopping:
                    break
                try:
                    self.check_watchdog()
                except Exception as exc:  # noqa: BLE001 - 单步异常不杀循环
                    logger.exception(f"委派看门狗巡检异常: {exc}")
        except asyncio.CancelledError:
            logger.debug("委派看门狗定时循环已取消")

    # -------------------- 事件观察与看门狗 --------------------

    def _lock_of(self, executor: str) -> asyncio.Lock:
        lock = self._locks.get(executor)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[executor] = lock
        return lock

    def _note_executor_activity(self, executor: str, at_ms: Optional[int] = None) -> None:
        """记录执行者一次真实工具或目标活动，同步推进其在做单的看门狗窗口。"""
        if not executor:
            return
        ts = self._clock() if at_ms is None else at_ms
        if ts > self._last_activity_ms.get(executor, 0):
            self._last_activity_ms[executor] = ts
        entry = self._watchdog.get(executor)
        if entry is not None and ts > entry.timer_base_ms:
            entry.timer_base_ms = ts

    async def _on_tool_result(self, event_name: str, payload: ToolResultPayload, source: str) -> None:
        """观察工具调用完成事件：执行者自己的 ReAct 循环调了工具即算有实际活动。"""
        del event_name, source
        caller = (payload.caller_source or "").strip()
        if not caller:
            return
        executor = caller.removesuffix("-react")
        if self._manager.get_agent_by_name(executor) is not None:
            self._note_executor_activity(executor)

    async def _on_task_changed(self, event_name: str, payload: TaskChangedPayload, source: str) -> None:
        """task.changed 回调：维护执行者活动时刻、巡检看门狗；委派走到终态时自动送达下一件待办。"""
        del event_name, source
        if not payload.alert:
            if payload.source == "provider" and payload.initiator:
                self._note_executor_activity(payload.initiator)
            elif payload.source == "agent" and payload.executor and payload.status not in ("accepted", "queued"):
                self._note_executor_activity(payload.executor)
        self.check_watchdog()
        if payload.source == "agent" and payload.status in TERMINAL_TASK_STATES and payload.executor:
            await self._drain_queue(payload.executor)

    def _has_active_provider_goals(self, executor: str) -> bool:
        """执行者名下此刻是否有正在跑的后台子目标（provider 型任务，如长距离出行、采集、建造）。"""
        if self._task_ledger is None:
            return False
        for task_id in self._task_ledger.active_task_ids():
            record = self._task_ledger.get(task_id)
            if (
                record is not None
                and record.source == "provider"
                and record.initiator == executor
                and record.status in IN_PROGRESS_TASK_STATES
            ):
                return True
        return False

    def _watchdog_track(self, executor: str, task_id: str, base_ms: int) -> None:
        self._watchdog[executor] = _WatchdogEntry(task_id=task_id, timer_base_ms=base_ms)
        self._last_activity_ms[executor] = base_ms

    def check_watchdog(self) -> None:
        """巡检全部执行者手上的【在做】委派：无在跑子目标且超时无工具/任务活动时提醒发起方定夺。"""
        if self._task_ledger is None or self._watchdog_timeout_ms <= 0:
            return
        now = self._clock()
        executors = set(self._watchdog)
        for task_id in self._task_ledger.active_task_ids():
            rec = self._task_ledger.get(task_id)
            if rec is not None and rec.source == "agent" and rec.tool == "framework_delegate":
                executors.add(rec.executor)
        for executor in sorted(executors):
            board = read_board(self._task_ledger, executor)
            in_hand = board.in_hand
            if in_hand is None:
                self._watchdog.pop(executor, None)
                continue
            entry = self._watchdog.get(executor)
            if entry is None or entry.task_id != in_hand.task_id:
                base = max(in_hand.delivered_at_ms, self._last_activity_ms.get(executor, 0)) or now
                entry = _WatchdogEntry(task_id=in_hand.task_id, timer_base_ms=base)
                self._watchdog[executor] = entry
            # 执行者名下还有后台目标在跑：正在干活，刷新基准且绝不误报停滞
            if self._has_active_provider_goals(executor):
                entry.timer_base_ms = now
                self._last_activity_ms[executor] = now
                continue
            reply_at = int(in_hand.snapshot.get(REPLY_NOTE_AT_KEY) or 0)
            last_active = max(entry.timer_base_ms, self._last_activity_ms.get(executor, 0), reply_at)
            if last_active > entry.timer_base_ms:
                entry.timer_base_ms = last_active
            waited_ms = now - entry.timer_base_ms
            if waited_ms < self._watchdog_timeout_ms:
                continue
            if entry.escalations >= _DELEGATION_WATCHDOG_MAX_ESCALATIONS:
                continue
            entry.escalations += 1
            entry.timer_base_ms = now
            self._watchdog_escalate(in_hand.record, entry.escalations, waited_ms)

    def _resolve_initiator_receiver(self, initiator: str, executor: str) -> Optional[BaseAgent]:
        """定位接收看门狗提醒的发起方 Agent。

        直接按名查名册优先；若发起方标识是内部调用源（如主播工作台的 ``planner-react`` 或运营 ``operator``）
        不在名册，则回退到名册中除执行者外唯一覆写了 ``receive_prompt`` 的 Agent（生产环境即主播 Agent），
        保证看门狗提醒不会因调用源别名而漏送。
        """
        direct = self._manager.get_agent_by_name(initiator)
        if direct is not None:
            return direct
        candidates: List[BaseAgent] = []
        for name in self._manager.list_agents():
            if name == executor:
                continue
            agent = self._manager.get_agent_by_name(name)
            if agent is not None and getattr(type(agent), "receive_prompt", None) is not BaseAgent.receive_prompt:
                candidates.append(agent)
        return candidates[0] if len(candidates) == 1 else None

    def _watchdog_escalate(self, record: TaskRecord, escalations: int, waited_ms: int) -> None:
        """向发起方递话提醒：带【在做】短编号、原话与三种定夺出口，不念内部任务号。"""
        waited_min = max(1, waited_ms // 60_000)
        own = self._quote_own_words(record.snapshot)
        summary = (
            f"[委派看门狗] {record.executor} 手上的【{IN_HAND_LABEL}】「{own}」"
            f"已 {waited_min} 分钟没有任何目标或工具活动、也未交付"
            f"（第 {escalations}/{_DELEGATION_WATCHDOG_MAX_ESCALATIONS} 次提醒）。"
            f"请定夺：若其实已做完，用 framework_task_settle(item='{IN_HAND_LABEL}', outcome='done') 结单；"
            f"若还要接着做，用 framework_prompt 催办；若先放下或不做，用 outcome='shelve' 先搁下或 'drop' 作废。"
        )
        receiver = self._resolve_initiator_receiver(record.initiator, record.executor)
        receive = getattr(receiver, "receive_prompt", None) if receiver is not None else None
        if callable(receive) and receive(content=summary, source="委派看门狗"):
            logger.warning(
                f"委派无活动提醒已送达 '{receiver.name}': task_id={record.task_id}，已 {waited_min} 分钟无活动"
            )
            return
        logger.warning(
            f"委派无活动提醒降级账本告警（发起方 '{record.initiator}' 无可用递话入口或拒收）: "
            f"task_id={record.task_id}，已 {waited_min} 分钟无活动"
        )
        if self._task_ledger is not None:
            self._task_ledger.alert_stall(record.task_id, summary=summary)

    # -------------------- 看板辅助与序号分配 --------------------

    def _alloc_backlog_no(self, executor: str, existing: int = 0) -> int:
        if existing > 0:
            if existing > self._backlog_nos.get(executor, 0):
                self._backlog_nos[executor] = existing
            return existing
        nxt = self._backlog_nos.get(executor, 0) + 1
        self._backlog_nos[executor] = nxt
        return nxt

    def _next_tail_seq(self) -> int:
        self._tail_seq += 1
        return self._tail_seq

    def _next_head_seq(self) -> int:
        self._head_seq -= 1
        return self._head_seq

    @staticmethod
    def _quote_own_words(snapshot: Dict[str, Any]) -> str:
        return str(
            snapshot.get("own_words") or own_words_of(str(snapshot.get("instruction") or "")) or "（快照无指令原文）"
        )

    def board_of(self, executor: str) -> Board:
        """读取 ``executor`` 当前的委派看板（在做 + 待办；只读）。"""
        return read_board(self._task_ledger, executor)

    def _clear_held_flags(self, executor: str, *, except_task_id: str = "") -> None:
        """有新委派送达执行者后，清掉同执行者待办件上的 held 标记，使搁下的件后续能正常轮到。"""
        if self._task_ledger is None:
            return
        board = read_board(self._task_ledger, executor)
        for item in board.backlog:
            if item.task_id != except_task_id and item.held:
                updated = {**item.snapshot, HELD_KEY: False}
                self._task_ledger.update(item.task_id, "queued", snapshot=updated)

    def _board_summary_lines(self, executor: str, board: Board) -> List[str]:
        current_ms = self._clock()
        lines: List[str] = []
        if board.in_hand is not None:
            lines.append(self._in_hand_line(board.in_hand, current_ms))
        else:
            lines.append(f"{IN_HAND_LABEL}：（空）")
        if board.backlog:
            lines.append("待办（按先后顺序）：")
            for item in board.backlog:
                waited_s = max(current_ms - item.queued_at_ms, 0) // 1000
                state = f"搁下了，做到：{item.shelved_progress or '未记进展'}" if item.shelved else "还没开工"
                lines.append(f"- {item.label}：「{item.own_words}」（{state}，排了 {waited_s} 秒）")
        return lines

    @staticmethod
    def _in_hand_line(item: BoardItem, current_ms: int) -> str:
        waited_s = max(current_ms - item.delivered_at_ms, 0) // 1000
        line = f"{IN_HAND_LABEL}：「{item.own_words}」（已做 {waited_s} 秒）"
        extras = {k: v for k, v in item.snapshot.items() if k not in _NOT_PROGRESS_KEYS}
        if extras:
            line += f"；已知进展：{extras}"
        return line

    def _queue_full_message(self, executor: str, board: Board) -> str:
        lines = [
            f"受理失败：目标 '{executor}' 的待办已排满 {_DELEGATION_QUEUE_LIMIT} 件，这件没排进去。",
            *self._board_summary_lines(executor, board),
            "等手上的事做完再派，不要重复派同一件事；不做的待办用 framework_task_settle(item='待办N', outcome='drop') 撤掉腾位置；"
            "真要停下手上的事立刻换做这件，用 when_busy='replace' 并填 current（'done' / 'shelve' / 'drop'）。",
        ]
        return "\n".join(lines)

    # -------------------- 目标解析 --------------------

    def resolve_target(
        self, target_name: str, caller: str, roster: Optional[Dict[str, str]]
    ) -> tuple[Optional[BaseAgent], Optional[_DelegationOutcome]]:
        """解析目标 Agent（委派/递话/结单共用）：留空唯一候选兜底 + 禁自派 + 名册校验。"""
        if not target_name:
            candidates = [name for name in (roster or {}) if name != caller]
            if len(candidates) != 1:
                detail = (
                    "当前没有能接收的 Agent（启用的游戏若不走委派，直接用它自己的操作工具）"
                    if not candidates
                    else f"当前有多个候选（{'、'.join(candidates)}）"
                )
                return None, _DelegationOutcome(
                    ok=False, error="not_found", message=f"受理失败：未指定目标，且{detail}——请点名目标注册名"
                )
            target_name = candidates[0]
        if target_name == (caller or "unknown"):
            return None, _DelegationOutcome(ok=False, error="refused", message="受理失败：目标不能是自己（自派被拒）")
        target = self._manager.get_agent_by_name(target_name)
        if target is None:
            return None, _DelegationOutcome(
                ok=False, error="not_found", message=f"受理失败：目标 Agent '{target_name}' 不在名册"
            )
        return target, None

    # -------------------- 送达、收手与待办推进 --------------------

    @staticmethod
    def _delivery_text(instruction: str, *, was_shelved: bool, progress: str) -> str:
        """构造送往执行者的指令文本：搁下过的件按新任务重送达，附上之前做到哪。"""
        if was_shelved:
            return compose_redelivery(instruction, progress)
        return instruction

    def _call_receive(self, target: BaseAgent, *, instruction: str, task_id: str) -> Optional[str]:
        receive = getattr(target, "receive_delegation", None)
        if not callable(receive):
            return f"目标 '{target.name}' 未实现接收入口（默认拒收）"
        return receive(instruction=instruction, task_id=task_id)

    async def _release_in_hand(self, target: BaseAgent, item: BoardItem, *, reason: str) -> Optional[str]:
        """让执行者对当前【在做】收手；异常记日志并按不支持（None）处理。"""
        try:
            return await target.release_delegation(item.task_id, reason=reason)
        except Exception as exc:  # noqa: BLE001 - 收手异常不崩受理链路
            logger.warning(f"目标 '{target.name}' 收手异常（task_id={item.task_id}）: {exc}", exc=True)
            return None

    async def _drain_queue(self, executor: str) -> None:
        async with self._lock_of(executor):
            await self._drain_unlocked(executor)

    async def _drain_unlocked(self, executor: str) -> None:
        """执行者手上空着时，把待办里排最前且未标 held 的那件送达（搁下过的件带进度按新任务送达）。"""
        if self._task_ledger is None:
            return
        while True:
            board = read_board(self._task_ledger, executor)
            if board.in_hand is not None:
                return
            nxt = board.next_ready()
            if nxt is None:
                return
            target = self._manager.get_agent_by_name(executor)
            if target is None:
                self._task_ledger.update(nxt.task_id, "failed", summary=f"送达失败：执行者 '{executor}' 不在名册")
                continue
            text = self._delivery_text(
                nxt.instruction, was_shelved=nxt.shelved or nxt.deliveries > 0, progress=nxt.shelved_progress
            )
            rejected = self._call_receive(target, instruction=text, task_id=nxt.task_id)
            if rejected:
                logger.info(f"待办件送达被拒（task_id={nxt.task_id}, executor={executor}）：{rejected}")
                self._task_ledger.update(nxt.task_id, "failed", summary=f"送达被目标拒收：{rejected}")
                continue
            ts = self._clock()
            snap = {
                **nxt.snapshot,
                DELIVERIES_KEY: nxt.deliveries + 1,
                DELIVERED_AT_KEY: ts,
                HELD_KEY: False,
            }
            summary = "搁下的事重新送达执行" if nxt.shelved else "轮到执行，已送达目标"
            self._task_ledger.update(nxt.task_id, "accepted", snapshot=snap, summary=summary)
            self._clear_held_flags(executor, except_task_id=nxt.task_id)
            self._watchdog_track(executor, nxt.task_id, ts)
            logger.info(f"待办件送达: {nxt.record.initiator} -> {executor}（task_id={nxt.task_id}，{summary}）")
            return

    def _deliver_fresh(self, target: BaseAgent, instruction: str, initiator: str) -> _DelegationOutcome:
        """直接送达一件全新委派并登记为【在做】。"""
        assert self._task_ledger is not None
        executor = target.name
        task_id = _generate_task_id()
        rejected = self._call_receive(target, instruction=instruction, task_id=task_id)
        if rejected:
            logger.info(f"delegate: Agent '{executor}' 拒收委派（{rejected}）")
            msg = rejected if rejected.startswith("受理失败") else f"受理失败：目标 '{executor}' 拒收（{rejected}）"
            return _DelegationOutcome(ok=False, error="refused", message=msg)
        ts = self._clock()
        snapshot: Dict[str, Any] = {
            "instruction": instruction,
            "own_words": own_words_of(instruction),
            DELIVERIES_KEY: 1,
            DELIVERED_AT_KEY: ts,
        }
        self._task_ledger.register(
            task_id=task_id,
            provider="framework",
            tool="framework_delegate",
            initiator=initiator,
            executor=executor,
            status="accepted",
            source="agent",
            snapshot=snapshot,
        )
        self._clear_held_flags(executor, except_task_id=task_id)
        self._watchdog_track(executor, task_id, ts)
        logger.info(f"委派受理（在做）: {initiator} -> {executor}（task_id={task_id}）")
        return _DelegationOutcome(
            ok=True, task_id=task_id, executor=executor, item=IN_HAND_LABEL, own_words=own_words_of(instruction)
        )

    def _deliver_backlog_item(self, target: BaseAgent, picked: BoardItem) -> _DelegationOutcome:
        """把待办里选中的 ``picked`` 送达执行者并转为【在做】。"""
        assert self._task_ledger is not None
        executor = target.name
        text = self._delivery_text(
            picked.instruction,
            was_shelved=picked.shelved or picked.deliveries > 0,
            progress=picked.shelved_progress,
        )
        rejected = self._call_receive(target, instruction=text, task_id=picked.task_id)
        if rejected:
            logger.info(f"待办提拔送达被拒（task_id={picked.task_id}, executor={executor}）：{rejected}")
            return _DelegationOutcome(
                ok=False, error="refused", message=f"受理失败：目标 '{executor}' 拒收（{rejected}）"
            )
        ts = self._clock()
        snap = {
            **picked.snapshot,
            DELIVERIES_KEY: picked.deliveries + 1,
            DELIVERED_AT_KEY: ts,
            HELD_KEY: False,
        }
        self._task_ledger.update(picked.task_id, "accepted", snapshot=snap, summary="从待办提上来开始做")
        self._clear_held_flags(executor, except_task_id=picked.task_id)
        self._watchdog_track(executor, picked.task_id, ts)
        logger.info(f"待办提拔为在做: {executor}（task_id={picked.task_id}）")
        return _DelegationOutcome(
            ok=True, task_id=picked.task_id, executor=executor, item=IN_HAND_LABEL, own_words=picked.own_words
        )

    def _enqueue_fresh(
        self, target: BaseAgent, instruction: str, initiator: str, *, front: bool = False
    ) -> _DelegationOutcome:
        """把一件新委派排进待办（front=True 排到最前，否则排到末尾）。"""
        assert self._task_ledger is not None
        executor = target.name
        task_id = _generate_task_id()
        ts = self._clock()
        no = self._alloc_backlog_no(executor)
        seq = self._next_head_seq() if front else self._next_tail_seq()
        base_snapshot: Dict[str, Any] = {
            "instruction": instruction,
            "own_words": own_words_of(instruction),
            BACKLOG_NO_KEY: no,
            BACKLOG_SEQ_KEY: seq,
            DELIVERIES_KEY: 0,
        }
        self._task_ledger.register(
            task_id=task_id,
            provider="framework",
            tool="framework_delegate",
            initiator=initiator,
            executor=executor,
            status="accepted",
            source="agent",
            snapshot=base_snapshot,
        )
        queued_snapshot = {**base_snapshot, QUEUED_AT_KEY: ts}
        board_preview = read_board(self._task_ledger, executor)
        position = 1 if front else len(board_preview.backlog) + 1
        self._task_ledger.update(
            task_id,
            "queued",
            summary=f"目标手上有事，排进待办（待办{no}，第 {position} 位）",
            snapshot=queued_snapshot,
        )
        board_after = read_board(self._task_ledger, executor)
        actual_pos = next((idx for idx, b in enumerate(board_after.backlog, start=1) if b.task_id == task_id), position)
        label = f"{BACKLOG_LABEL_PREFIX}{no}"
        logger.info(f"委派入待办: {initiator} -> {executor}（task_id={task_id}, {label}, 第 {actual_pos} 位）")
        return _DelegationOutcome(
            ok=True,
            task_id=task_id,
            executor=executor,
            item=label,
            own_words=own_words_of(instruction),
            queued=True,
            queue_position=actual_pos,
        )

    def _shelve_record(self, item: BoardItem, *, progress: str, held: bool) -> str:
        """把一件在做单放回待办最前，保留做到哪了；返回它的待办短编号。"""
        assert self._task_ledger is not None
        executor = item.record.executor
        ts = self._clock()
        no = self._alloc_backlog_no(executor, existing=item.backlog_no)
        seq = self._next_head_seq()
        snap = {
            **item.snapshot,
            BACKLOG_NO_KEY: no,
            BACKLOG_SEQ_KEY: seq,
            QUEUED_AT_KEY: ts,
            SHELVED_AT_KEY: ts,
            SHELVED_PROGRESS_KEY: progress,
            HELD_KEY: held,
        }
        note = progress or "未记具体进展"
        self._task_ledger.update(item.task_id, "queued", snapshot=snap, summary=f"先搁下回待办，做到：{note}")
        return f"{BACKLOG_LABEL_PREFIX}{no}"

    def _settle_old_in_hand(self, item: BoardItem, *, current: str, progress: str, held_if_shelved: bool) -> None:
        """按 ``current``（done / shelve / drop）把已收手的旧在做单落账。"""
        assert self._task_ledger is not None
        if current == "done":
            detail = f"：{progress}" if progress else ""
            self._task_ledger.update(item.task_id, "closed_by_initiator", summary=f"发起方认定已完成结单{detail}")
        elif current == "drop":
            detail = f"：{progress}" if progress else ""
            self._task_ledger.update(item.task_id, "cancelled", summary=f"换活作废不做了{detail}")
        else:
            self._shelve_record(item, progress=progress, held=held_if_shelved)

    def _rollback_in_hand(self, target: BaseAgent, old_item: BoardItem, progress: str, reject_reason: str) -> str:
        """新件送达被拒收时，把刚收手的旧件连同收手进度一并送回执行者；连送回都被拒则搁回待办最前保底不丢。"""
        assert self._task_ledger is not None
        executor = target.name
        # 旧件在第 1 步已经调过 release_delegation 清了在途目标和历史：不论进度是否为空，
        # 一律按重送达格式把"做到哪了"与核对现场提示拼进指令，不让执行侧从零盲做
        restore_text = compose_redelivery(old_item.instruction, progress or old_item.shelved_progress)
        rollback_rejected = self._call_receive(target, instruction=restore_text, task_id=old_item.task_id)
        if not rollback_rejected:
            ts = self._clock()
            snap = {
                **old_item.snapshot,
                DELIVERIES_KEY: max(1, old_item.deliveries),
                DELIVERED_AT_KEY: ts,
            }
            if progress:
                snap[SHELVED_PROGRESS_KEY] = progress
            self._task_ledger.update(old_item.task_id, "accepted", snapshot=snap, summary="新件被拒，恢复原在做委派")
            self._watchdog_track(executor, old_item.task_id, ts)
            logger.info(f"新件被拒收，已将原委派 {old_item.task_id} 送回 '{executor}' 继续做")
            return f"受理失败：目标 '{executor}' 拒收新委派（{reject_reason}）；原【{IN_HAND_LABEL}】「{old_item.own_words}」已送回继续执行。"
        label = self._shelve_record(old_item, progress=progress, held=False)
        self._watchdog.pop(executor, None)
        logger.warning(
            f"新件被拒收且原委派 {old_item.task_id} 送回也被拒（{rollback_rejected}），已搁回 {label} 保底不丢"
        )
        return (
            f"受理失败：目标 '{executor}' 拒收新委派（{reject_reason}），"
            f"原委派送回也未接（{rollback_rejected}），已放回【{label}】最前等待连接恢复。"
        )

    # -------------------- 公开受理入口：delegate 与 settle --------------------

    async def accept(
        self,
        target: BaseAgent,
        instruction: str,
        initiator: str,
        *,
        item_ref: str = "",
        when_busy: str = "queue",
        current: str = "",
        progress: str = "",
        urgent: bool = False,
    ) -> _DelegationOutcome:
        """接单主入口（按执行者加锁串行）：闲时直派 / 忙时排待办 / 忙时收手结旧换新。"""
        if self._task_ledger is None:
            return _DelegationOutcome(
                ok=False, error="no_ledger", message="任务基建未装配（task_ledger 未注入），委派不可用"
            )
        executor = target.name
        # 兼容 urgent=True：等价于换成这件 + 在做的先搁下
        mode = when_busy.strip() or "queue"
        old_outcome = current.strip()
        if urgent:
            if not when_busy.strip() or mode == "queue":
                mode = "replace"
            if not old_outcome:
                old_outcome = "shelve"
        if mode not in _VALID_WHEN_BUSY:
            return _DelegationOutcome(
                ok=False,
                error="refused",
                message=f"受理失败：when_busy 只能是 'queue' 或 'replace'（收到 '{mode}'）",
            )

        self.check_watchdog()
        async with self._lock_of(executor):
            board = read_board(self._task_ledger, executor)
            picked: Optional[BoardItem] = None
            if item_ref.strip():
                picked = find_item(board, item_ref)
                if picked is None:
                    lines = self._board_summary_lines(executor, board)
                    return _DelegationOutcome(
                        ok=False,
                        error="refused",
                        message=f"受理失败：在 '{executor}' 的看板上找不到 '{item_ref}'。\n" + "\n".join(lines),
                    )
                if picked.in_hand:
                    return _DelegationOutcome(
                        ok=False,
                        error="refused",
                        message=(
                            f"受理失败：「{picked.own_words}」已经在【{IN_HAND_LABEL}】了，不用再派；"
                            "补充同一件事的要求用 framework_prompt，结单用 framework_task_settle。"
                        ),
                    )

            # 分支 1：目标手上没有【在做】
            if board.in_hand is None:
                if picked is not None:
                    return self._deliver_backlog_item(target, picked)
                if mode == "queue" and board.next_ready() is not None:
                    if len(board.backlog) >= _DELEGATION_QUEUE_LIMIT:
                        return _DelegationOutcome(
                            ok=False, error="refused", message=self._queue_full_message(executor, board)
                        )
                    outcome = self._enqueue_fresh(target, instruction, initiator, front=False)
                    await self._drain_unlocked(executor)
                    return outcome
                return self._deliver_fresh(target, instruction, initiator)

            # 分支 2：目标手上有【在做】且选排后面（queue）
            if mode == "queue":
                if picked is not None:
                    snap = {**picked.snapshot, BACKLOG_SEQ_KEY: self._next_head_seq(), HELD_KEY: False}
                    self._task_ledger.update(picked.task_id, "queued", snapshot=snap, summary="提到待办最前")
                    return _DelegationOutcome(
                        ok=True,
                        task_id=picked.task_id,
                        executor=executor,
                        item=picked.label,
                        own_words=picked.own_words,
                        queued=True,
                        queue_position=1,
                    )
                if len(board.backlog) >= _DELEGATION_QUEUE_LIMIT:
                    logger.info(f"委派拒受理（待办满 {_DELEGATION_QUEUE_LIMIT} 件）: {initiator} -> {executor}")
                    return _DelegationOutcome(
                        ok=False, error="refused", message=self._queue_full_message(executor, board)
                    )
                return self._enqueue_fresh(target, instruction, initiator, front=False)

            # 分支 3：目标手上有【在做】且选换成这件（replace）——必须同时交代在做那件怎么结
            in_hand = board.in_hand
            if old_outcome not in _VALID_OUTCOMES:
                return _DelegationOutcome(
                    ok=False,
                    error="refused",
                    message=(
                        f"受理失败：目标 '{executor}' 手上有【{IN_HAND_LABEL}】「{in_hand.own_words}」。"
                        "选 when_busy='replace' 换活时必须用 current 交代在做那件："
                        "'done'（做完了）/ 'shelve'（先搁下）/ 'drop'（不做了）；"
                        "若不打断手上的事，改用 when_busy='queue' 排进待办。"
                    ),
                )

            released = await self._release_in_hand(target, in_hand, reason=f"换做新委派（原件 {old_outcome}）")
            if released is None:
                # 执行者不支持收手：搁下语义降级为新件置顶排进待办最前；结单/作废试硬取消兜底
                if old_outcome == "shelve":
                    if picked is not None:
                        snap = {**picked.snapshot, BACKLOG_SEQ_KEY: self._next_head_seq(), HELD_KEY: False}
                        self._task_ledger.update(
                            picked.task_id, "queued", snapshot=snap, summary="目标不支持收手，提到待办最前"
                        )
                        return _DelegationOutcome(
                            ok=True,
                            task_id=picked.task_id,
                            executor=executor,
                            item=picked.label,
                            queued=True,
                            queue_position=1,
                        )
                    logger.info(f"目标 '{executor}' 不支持收手，换活降级为置顶待办")
                    return self._enqueue_fresh(target, instruction, initiator, front=True)
                cancelled = await target.cancel_task_now(in_hand.task_id, source=initiator, reason=progress)
                if not cancelled:
                    return _DelegationOutcome(
                        ok=False,
                        error="refused",
                        message=f"受理失败：目标 '{executor}' 不支持中途收手，无法替换当前【{IN_HAND_LABEL}】",
                    )
                released = ""

            merged_progress = _merge_progress(progress, released)
            # 先试送达新件：新件若被拒收，立即把刚收手的旧件原样送回执行者
            if picked is not None:
                new_text = self._delivery_text(
                    picked.instruction,
                    was_shelved=picked.shelved or picked.deliveries > 0,
                    progress=picked.shelved_progress,
                )
                new_task_id = picked.task_id
            else:
                new_text = instruction
                new_task_id = _generate_task_id()

            rejected = self._call_receive(target, instruction=new_text, task_id=new_task_id)
            if rejected:
                msg = self._rollback_in_hand(target, in_hand, merged_progress, rejected)
                return _DelegationOutcome(ok=False, error="refused", message=msg)

            # 新件已送达：先按 current 结掉或搁下旧件，再把新件落账为唯一【在做】
            self._settle_old_in_hand(in_hand, current=old_outcome, progress=merged_progress, held_if_shelved=False)
            ts = self._clock()
            if picked is not None:
                snap = {
                    **picked.snapshot,
                    DELIVERIES_KEY: picked.deliveries + 1,
                    DELIVERED_AT_KEY: ts,
                    HELD_KEY: False,
                }
                self._task_ledger.update(
                    picked.task_id, "accepted", snapshot=snap, summary="替换原任务，从待办提为在做"
                )
            else:
                self._task_ledger.register(
                    task_id=new_task_id,
                    provider="framework",
                    tool="framework_delegate",
                    initiator=initiator,
                    executor=executor,
                    status="accepted",
                    source="agent",
                    snapshot={
                        "instruction": instruction,
                        "own_words": own_words_of(instruction),
                        DELIVERIES_KEY: 1,
                        DELIVERED_AT_KEY: ts,
                    },
                )
            self._clear_held_flags(executor, except_task_id=new_task_id)
            self._watchdog_track(executor, new_task_id, ts)
            logger.info(
                f"委派换活成功: {initiator} -> {executor}（旧 {in_hand.task_id} -> {old_outcome}，新 {new_task_id} 在做）"
            )
            new_own = picked.own_words if picked is not None else own_words_of(instruction)
            return _DelegationOutcome(
                ok=True, task_id=new_task_id, executor=executor, item=IN_HAND_LABEL, own_words=new_own
            )

    async def delegate(
        self,
        *,
        target_name: str,
        instruction: str,
        caller: str,
        roster: Optional[Dict[str, str]] = None,
        item_ref: str = "",
        when_busy: str = "queue",
        current: str = "",
        progress: str = "",
        urgent: bool = False,
    ) -> _DelegationOutcome:
        """受理一次委派（完整前置序列）：基建在位 → 解析目标 → 接单。"""
        if self._task_ledger is None:
            return _DelegationOutcome(
                ok=False, error="no_ledger", message="任务基建未装配（task_ledger 未注入），委派不可用"
            )
        target, failure = self.resolve_target(target_name, caller, roster)
        if failure is not None:
            return failure
        assert target is not None  # 解析成功时必有实例（类型收窄）
        return await self.accept(
            target,
            instruction,
            caller or "unknown",
            item_ref=item_ref,
            when_busy=when_busy,
            current=current,
            progress=progress,
            urgent=urgent,
        )

    async def settle(
        self,
        *,
        target_name: str,
        item_ref: str,
        outcome: str,
        caller: str,
        progress: str = "",
        roster: Optional[Dict[str, str]] = None,
    ) -> _SettleOutcome:
        """按短编号（'在做' / '待办N'）或内部任务号结单、搁下或撤单。"""
        if self._task_ledger is None:
            return _SettleOutcome(
                ok=False, error="no_ledger", message="任务基建未装配（task_ledger 未注入），结单不可用"
            )
        ref = item_ref.strip()
        if not ref:
            return _SettleOutcome(
                ok=False,
                error="refused",
                message="task_settle 需要 item（填短编号 '在做' 或 '待办1'、'待办2'……）",
            )
        action = outcome.strip()
        if action not in _VALID_OUTCOMES:
            return _SettleOutcome(
                ok=False,
                error="refused",
                message=f"task_settle 的 outcome 只能是 'done'、'shelve' 或 'drop'（收到 '{action}'）",
            )
        # 拒绝执行方内部子目标号（如 maicraft-goal-489），避免把游戏子目标误当委派单
        is_short_label = ref == IN_HAND_LABEL or (
            ref.removeprefix(BACKLOG_LABEL_PREFIX).strip().isdigit()
            and int(ref.removeprefix(BACKLOG_LABEL_PREFIX).strip()) > 0
        )
        if not is_short_label and not ref.startswith("deleg_"):
            return _SettleOutcome(
                ok=False,
                error="refused",
                message=(
                    f"只能处置委派单（填 '{IN_HAND_LABEL}'、'{BACKLOG_LABEL_PREFIX}1' 等短编号），'{ref}' 是执行方内部目标号，"
                    "不归这里管——让执行方用它自己的目标工具（如 maicraft_goal 的 cancel）收场"
                ),
            )

        resolved_target = target_name.strip()
        if not resolved_target and ref.startswith("deleg_"):
            rec = self._task_ledger.get(ref)
            if rec is not None and rec.source == "agent":
                resolved_target = rec.executor
            else:
                return _SettleOutcome(
                    ok=False,
                    error="not_found",
                    message=f"任务不存在: '{ref}'（未知委派号或已终态）",
                )
        target, failure = self.resolve_target(resolved_target, caller, roster)
        if failure is not None:
            return _SettleOutcome(ok=False, error=failure.error, message=failure.message)
        assert target is not None
        executor = target.name

        async with self._lock_of(executor):
            board = read_board(self._task_ledger, executor)
            found = find_item(board, ref)
            if found is None:
                if ref.startswith("deleg_"):
                    return _SettleOutcome(
                        ok=False,
                        error="not_found",
                        message=f"任务不存在: '{ref}'（未知委派号或已终态）",
                    )
                lines = self._board_summary_lines(executor, board)
                return _SettleOutcome(
                    ok=False,
                    error="not_found",
                    message=f"在 '{executor}' 的看板上找不到 '{ref}'（可能已结单）。\n" + "\n".join(lines),
                )

            # 情况 1：处置的是【待办】里的件——执行者手上没有这件，绝不触碰执行者（不影响正在做的另一件）
            if not found.in_hand:
                if action == "shelve":
                    if found.shelved and progress.strip():
                        snap = {**found.snapshot, SHELVED_PROGRESS_KEY: progress.strip()}
                        self._task_ledger.update(found.task_id, "queued", snapshot=snap)
                        return _SettleOutcome(
                            ok=True,
                            task_id=found.task_id,
                            executor=executor,
                            item=found.label,
                            outcome=action,
                            status="queued",
                        )
                    return _SettleOutcome(
                        ok=False,
                        error="refused",
                        message=(
                            f"【{found.label}】「{found.own_words}」本来就在待办里（还没在做），不用再搁下；"
                            "不做了请用 outcome='drop'，已经不用做且算完成请用 outcome='done'。"
                        ),
                    )
                status = "closed_by_initiator" if action == "done" else "cancelled"
                summary = (
                    f"发起方认定已完成（待办直接结单{f'：{progress}' if progress else ''}）"
                    if action == "done"
                    else f"待办中被撤（还没送到执行方，source={caller or '未知'}{f'，{progress}' if progress else ''}）"
                )
                self._task_ledger.update(found.task_id, status, summary=summary)
                logger.info(f"待办件已结单: {found.task_id} ({found.label}) -> {status}")
                return _SettleOutcome(
                    ok=True,
                    task_id=found.task_id,
                    executor=executor,
                    item=found.label,
                    outcome=action,
                    status=status,
                )

            # 情况 2：处置的是【在做】那件——先让执行者收手，再落账并自动送达下一件就绪待办
            released = await self._release_in_hand(target, found, reason=progress or action)
            if released is None:
                if action == "shelve":
                    return _SettleOutcome(
                        ok=False,
                        error="refused",
                        message=f"目标 '{executor}' 不支持中途收手，无法将【{IN_HAND_LABEL}】搁回待办",
                    )
                cancelled = await target.cancel_task_now(found.task_id, source=caller or "unknown", reason=progress)
                if not cancelled:
                    return _SettleOutcome(
                        ok=False,
                        error="not_found",
                        message=f"结单失败：任务 '{found.task_id}' 不在执行者 '{executor}' 的追踪范围（未知号或已终态）",
                    )
                released = ""

            merged = _merge_progress(progress, released)
            if action == "shelve":
                new_label = self._shelve_record(found, progress=merged, held=True)
                self._watchdog.pop(executor, None)
                await self._drain_unlocked(executor)
                logger.info(f"在做委派先搁下: {found.task_id} -> {new_label}（{executor}）")
                return _SettleOutcome(
                    ok=True,
                    task_id=found.task_id,
                    executor=executor,
                    item=new_label,
                    outcome=action,
                    status="queued",
                )

            status = "closed_by_initiator" if action == "done" else "cancelled"
            if self._task_ledger.get(found.task_id) is not None:
                summary = (
                    f"发起方认定已完成：{merged}"
                    if action == "done" and merged
                    else (
                        "发起方认定已完成结单"
                        if action == "done"
                        else (f"发起方撤单不做了（source={caller or '未知'}{f'，{progress}' if progress else ''}）")
                    )
                )
                self._task_ledger.update(found.task_id, status, summary=summary)
            self._watchdog.pop(executor, None)
            await self._drain_unlocked(executor)
            logger.info(f"在做委派已结单: {found.task_id} -> {status}（{executor}）")
            return _SettleOutcome(
                ok=True,
                task_id=found.task_id,
                executor=executor,
                item=IN_HAND_LABEL,
                outcome=action,
                status=status,
            )


# 兼容内部别名
_DelegationCore = DelegationCore


class AgentControl:
    """框架级控制——直接调用接口（不进 ToolRegistry，供 Dashboard REST 控制面使用）。"""

    def __init__(
        self,
        manager: AgentManager,
        task_ledger: Optional[TaskLedger] = None,
        event_bus: Optional[Any] = None,
        clock: Optional[Callable[[], int]] = None,
        *,
        delegation_core: Optional[DelegationCore] = None,
    ) -> None:
        self._manager = manager
        self._task_ledger = (
            task_ledger
            if task_ledger is not None
            else (delegation_core._task_ledger if delegation_core is not None else None)
        )
        self._event_bus = event_bus
        self._delegation = delegation_core or DelegationCore(manager, self._task_ledger, event_bus, clock)

    @property
    def delegation_core(self) -> DelegationCore:
        return self._delegation

    async def pause(self, name: str) -> bool:
        agent = self._manager.get_agent_by_name(name)
        if agent is None:
            logger.warning(f"pause_agent: 未找到 Agent '{name}'")
            return False
        await agent.pause()
        return True

    async def resume(self, name: str) -> bool:
        agent = self._manager.get_agent_by_name(name)
        if agent is None:
            logger.warning(f"resume_agent: 未找到 Agent '{name}'")
            return False
        await agent.resume()
        return True

    async def shutdown(self, name: str) -> bool:
        agent = self._manager.get_agent_by_name(name)
        if agent is None:
            logger.warning(f"shutdown_agent: 未找到 Agent '{name}'")
            return False
        await agent.shutdown()
        return True

    async def prompt(self, name: str, content: str, *, source: str = "operator") -> dict:
        """向指定 Agent 递话（控制面直调，供运营 REST）。"""
        agent = self._manager.get_agent_by_name(name)
        if agent is None:
            logger.warning(f"prompt: 未找到 Agent '{name}'")
            return {"ok": False, "error": "not_found", "message": f"Agent 不存在: {name}"}
        delivered = agent.receive_prompt(content=content, source=source)
        if not delivered:
            logger.info(f"prompt: Agent '{name}' 拒收递话（source={source}）")
            return {"ok": False, "error": "refused", "message": f"Agent '{name}' 拒收递话（未实现消化通道或留言已满）"}
        _emit_prompted(self._event_bus, target=name, content=content, source=source)
        return {"ok": True, "delivered": True, "executor": name}

    async def cancel_task(self, name: str, task_id: str, *, source: str = "operator", reason: str = "") -> dict:
        """硬取消指定 Agent 的任务（控制面直调，供运营 REST）。

        账本在位且该任务是委派单时走统一 ``DelegationCore.settle(outcome='drop')``：
        待办件直接移除不碰执行者，在做件收手后自动送达下一件待办；
        无账本或非委派任务走执行 Agent 的 ``cancel_task_now``。
        """
        agent = self._manager.get_agent_by_name(name)
        if agent is None:
            logger.warning(f"cancel_task: 未找到 Agent '{name}'")
            return {"ok": False, "error": "agent_not_found", "message": f"Agent 不存在: {name}"}
        if self._task_ledger is not None:
            rec = self._task_ledger.get(task_id)
            if rec is not None and rec.source == "agent" and rec.tool == "framework_delegate":
                settled = await self._delegation.settle(
                    target_name=name, item_ref=task_id, outcome="drop", caller=source, progress=reason
                )
                if settled.ok:
                    return {"ok": True, "cancelled": True}
                return {"ok": False, "error": "task_not_found", "message": settled.message}
        cancelled = await agent.cancel_task_now(task_id, source=source, reason=reason)
        if not cancelled:
            logger.info(f"cancel_task: Agent '{name}' 拒收取消（task_id={task_id}）")
            return {
                "ok": False,
                "error": "task_not_found",
                "message": f"任务不存在或已终态: {task_id}（Agent '{name}' 未在追踪）",
            }
        return {"ok": True, "cancelled": True}

    async def stop_actions(self, name: str, reason: str = "", *, cancel_delegations: bool = False) -> dict:
        """急停指定 Agent（控制面直调，供运营 REST）。"""
        agent = self._manager.get_agent_by_name(name)
        if agent is None:
            logger.warning(f"stop_actions: 未找到 Agent '{name}'")
            return {"ok": False, "error": "agent_not_found", "message": f"Agent 不存在: {name}"}
        stopped = await agent.stop_actions(reason, cancel_delegations=cancel_delegations)
        if not stopped:
            logger.info(f"stop_actions: Agent '{name}' 不支持急停")
            return {"ok": False, "error": "not_supported", "message": f"Agent '{name}' 不支持急停"}
        return {"ok": True, "stopped": True}

    async def delegate(
        self,
        name: str,
        instruction: str,
        *,
        initiator: str = "operator",
        urgent: bool = False,
        when_busy: str = "queue",
        current: str = "",
        progress: str = "",
    ) -> dict:
        """运营直派任务（控制面直调，供 REST）：与主播委派共用同一个 ``DelegationCore``。"""
        outcome = await self._delegation.delegate(
            target_name=name,
            instruction=instruction,
            caller=initiator,
            when_busy=when_busy,
            current=current,
            progress=progress,
            urgent=urgent,
        )
        if outcome.ok:
            receipt: dict = {
                "ok": True,
                "accepted": True,
                "task_id": outcome.task_id,
                "executor": outcome.executor,
                "item": outcome.item,
            }
            if outcome.queued:
                receipt["queued"] = True
                receipt["queue_position"] = outcome.queue_position
            return receipt
        logger.info(f"delegate: 运营直派未受理（{outcome.error}）: {initiator} -> {name}")
        return {"ok": False, "error": outcome.error, "message": outcome.message}

    def list_agents(self) -> List[str]:
        return self._manager.list_agents()

    def state_of(self, name: str) -> Optional[dict]:
        agent = self._manager.get_agent_by_name(name)
        if agent is None:
            return None
        alive = self._manager.is_agent_alive(name)
        return {
            "name": agent.name,
            "state": agent.state.value,
            "heartbeat_ms": agent.heartbeat.last_heartbeat_ms,
            "is_alive": alive if alive is not None else agent.is_alive(),
            "restart_count": agent.restart_count,
        }


# -------------------- Provider：注册进 ToolRegistry --------------------


@dataclass(slots=True)
class AgentControlProvider(BaseToolProvider):
    """把 framework LLM 工具面（delegate / prompt / task_status / task_settle）注册到 ToolRegistry。"""

    manager: AgentManager
    task_ledger: Optional[TaskLedger] = None
    event_bus: Optional[Any] = None
    clock: Optional[Callable[[], int]] = None
    delegation_core: Optional[DelegationCore] = None
    _delegation: DelegationCore = field(init=False, repr=False)

    category: ClassVar[str] = "framework"

    def __post_init__(self) -> None:
        if self.delegation_core is not None:
            self._delegation = self.delegation_core
            if self.task_ledger is None:
                self.task_ledger = self.delegation_core._task_ledger
        else:
            self._delegation = DelegationCore(self.manager, self.task_ledger, self.event_bus, self.clock)

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    async def setup(self) -> None:
        """随 ToolRegistry.start_providers 启动看门狗低频巡检循环。"""
        self._delegation.start()

    async def cleanup(self) -> None:
        """随 ToolRegistry.stop_providers 停止看门狗低频巡检循环。"""
        await self._delegation.stop()

    def list_tools(self) -> Iterable[ToolSpec]:
        """委派 + 递话 + 任务状态 + 任务结单工具（恒四件）。"""
        return [self._delegate_spec(), self._prompt_spec(), _TASK_STATUS_SPEC, _TASK_SETTLE_SPEC]

    def _roster(self, hook: str) -> Dict[str, str]:
        """当前名册 {注册名: 描述}，仅含覆写了 ``hook`` 接收入口的 Agent。"""
        roster: Dict[str, str] = {}
        list_agents = getattr(self.manager, "list_agents", None)
        descriptions = getattr(self.manager, "descriptions", {}) or {}
        get_agent = getattr(self.manager, "get_agent_by_name", None)
        if not (callable(list_agents) and callable(get_agent)):
            return roster
        base_fn = getattr(BaseAgent, hook, None)
        if base_fn is None:
            return roster
        for name in list_agents():
            agent = get_agent(name)
            if agent is None:
                continue
            if getattr(type(agent), hook, None) is base_fn:
                continue
            roster[name] = str(descriptions.get(name, "") or "")
        return roster

    def _agent_target_description(self, roster: Dict[str, str]) -> str:
        if roster:
            listed = "；".join(f"{name}={desc or '（无描述）'}" for name, desc in roster.items())
            return (
                f"目标 Agent 注册名（当前可委派：{listed}；留空 = 当前唯一启用的游戏 Agent，"
                "有多个候选时必须点名；不能指定自己）"
            )
        return "目标 Agent 注册名（当前没有能接收的 Agent：启用的游戏若不走委派，直接用它自己的操作工具）"

    def has_tools_for(self, agent_name: str) -> bool:
        """存在除调用方以外能接收委派的 Agent 时，才把委派四件暴露进该 Agent 的 LLM 工具表。

        纯杂谈场（仅启用主播）或仅启用遥控型游戏时没有委派接收方，隐藏工具定义避免误调；
        注册表直连 invoke 与运营全集（for_agent=None）仍保留全部四件。
        """
        return any(name != agent_name for name in self._roster("receive_delegation"))

    def guide_for(self, agent_name: str) -> str:
        """framework 工具面的使用说明：有能接收委派的 Agent 才给，否则空串。"""
        roster = {name: desc for name, desc in self._roster("receive_delegation").items() if name != agent_name}
        if not roster:
            return ""
        listed = "；".join(f"{name}={desc or '（无描述）'}" for name, desc in sorted(roster.items()))
        return (
            f"委派型游戏（可以把一件事交给它在后台做）：{listed}。"
            "每个目标任何时刻至多一件【在做】，其余在【待办】。"
            "用 framework_delegate 安排新的一件事或把待办提上来（目标忙时 when_busy 选 'queue' 排后面，"
            "或选 'replace' 换活并用 current 交代在做那件是 'done' 做完了 / 'shelve' 先搁下 / 'drop' 不做了）；"
            "用 framework_task_settle 报短编号（'在做'、'待办1'……）直接结单、先搁下或撤单；"
            "用 framework_prompt 只给当前【在做】的同一件事补充要求（kind='requirement'）或在聊天里回话（kind='chat'），不拿它换活；"
            "进度以它的上报与 framework_task_status 的查询结果为准，不要自己推测。"
        )

    def _delegate_spec(self) -> ToolSpec:
        roster = self._roster("receive_delegation")
        schema: Dict[str, Any] = {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": self._agent_target_description(roster)},
                "instruction": {
                    "type": "string",
                    "description": (
                        "工作指令（自然语言：目标与完成标准，不规定步骤与次序）。"
                        "新派一件事时填；把【待办】里已有的一件提上来做时可省略并改填 item"
                    ),
                },
                "item": {
                    "type": "string",
                    "description": "要把【待办】里哪一件提上来做（填短编号，如 '待办1'、'待办2'；新派一件事时不填）",
                },
                "when_busy": {
                    "type": "string",
                    "enum": ["queue", "replace"],
                    "description": (
                        "目标手上有【在做】时怎么办：'queue'（默认）排进待办后面；"
                        "'replace' 停下手上那件换成这件（选 'replace' 时必须填 current 交代在做那件怎么结）"
                    ),
                },
                "current": {
                    "type": "string",
                    "enum": ["done", "shelve", "drop"],
                    "description": (
                        "when_busy='replace' 时必填，交代当前【在做】怎么结："
                        "'done'=已经做完了结单，'shelve'=先搁下放回待办最前，'drop'=不做了作废"
                    ),
                },
                "progress": {
                    "type": "string",
                    "description": "选填：current='shelve' 时记一句在做那件做到哪了（不填则采用执行侧回报的进度）",
                },
                "urgent": {
                    "type": "boolean",
                    "description": "急事简写（可选）：设为 true 等同于 when_busy='replace' 且默认 current='shelve'",
                },
            },
        }
        if roster:
            schema["properties"]["agent"]["enum"] = sorted(roster)
        return ToolSpec(
            name="delegate",
            description=_DELEGATE_DESCRIPTION,
            parameters_schema=schema,
            kind="sync",
            provider=PROVIDER_NAME,
        )

    def _prompt_spec(self) -> ToolSpec:
        roster = self._roster("receive_prompt")
        schema: Dict[str, Any] = {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": self._agent_target_description(roster)},
                "content": {"type": "string", "description": "递话内容（自然语言：补充给手上的事的要求，或聊天回话）"},
                "kind": {
                    "type": "string",
                    "enum": ["requirement", "chat"],
                    "description": (
                        "递话种类：'requirement'（默认）给当前【在做】的同一件事补充要求；"
                        "'chat' 只是在游戏聊天里回一句话、不改手上的事"
                    ),
                },
            },
            "required": ["content"],
        }
        if roster:
            schema["properties"]["agent"]["enum"] = sorted(roster)
        return ToolSpec(
            name="prompt",
            description=_PROMPT_DESCRIPTION,
            parameters_schema=schema,
            kind="sync",
            provider=PROVIDER_NAME,
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        args = invocation.arguments or {}
        name = invocation.tool_name
        try:
            if name == "framework_delegate":
                return await self._invoke_delegate(args, caller=invocation.source)
            if name == "framework_prompt":
                return self._invoke_prompt(args, caller=invocation.source)
            if name == "framework_task_status":
                return self._invoke_task_status(args, caller=invocation.source)
            if name == "framework_task_settle":
                return await self._invoke_task_settle(args, caller=invocation.source)
            return ToolExecutionResult(
                tool_name=name,
                success=False,
                error_message=f"未知 AgentControl 工具 '{name}'",
            )
        except Exception as exc:  # noqa: BLE001 - 边界兜底
            logger.exception(f"AgentControl 工具 '{name}' 执行失败: {exc}")
            return ToolExecutionResult(
                tool_name=name,
                success=False,
                error_message=f"{type(exc).__name__}: {exc}",
            )

    async def _invoke_delegate(self, args: Dict[str, Any], *, caller: str) -> ToolExecutionResult:
        started_ms = now_ms()
        instruction = str(args.get("instruction", "") or "").strip()
        item_ref = str(args.get("item", "") or "").strip()
        if not instruction and not item_ref:
            return business_fail_result(
                "framework_delegate",
                started_ms,
                "delegate 需要 instruction（新事的自然语言指令）或 item（待办短编号，如 '待办1'）",
            )
        raw_instruction = str(args.get("instruction", "") or "")
        outcome = await self._delegation.delegate(
            target_name=str(args.get("agent", "") or ""),
            instruction=raw_instruction,
            caller=caller,
            roster=self._roster("receive_delegation"),
            item_ref=item_ref,
            when_busy=str(args.get("when_busy", "") or "queue"),
            current=str(args.get("current", "") or ""),
            progress=str(args.get("progress", "") or ""),
            urgent=bool(args.get("urgent", False)),
        )
        if outcome.ok:
            receipt: Dict[str, Any] = {
                "accepted": True,
                "task_id": outcome.task_id,
                "executor": outcome.executor,
                "item": outcome.item,
            }
            if outcome.own_words:
                receipt["own_words"] = outcome.own_words
            if outcome.queued:
                receipt["queued"] = True
                receipt["queue_position"] = outcome.queue_position
                receipt["note"] = (
                    f"目标手上有事，这件已排进待办（{outcome.item}，第 {outcome.queue_position} 位），轮到时自动送达；不要重复派。"
                )
            return ToolExecutionResult(
                tool_name="framework_delegate",
                success=True,
                structured_content=receipt,
            )
        if outcome.error == "no_ledger":
            return ToolExecutionResult(
                tool_name="framework_delegate",
                success=False,
                error_message=outcome.message,
            )
        return business_fail_result("framework_delegate", started_ms, outcome.message)

    def _invoke_prompt(self, args: Dict[str, Any], *, caller: str) -> ToolExecutionResult:
        started_ms = now_ms()
        content = str(args.get("content", "") or "")
        if not content:
            return business_fail_result("framework_prompt", started_ms, "prompt 需要 content（递话文本）")
        kind = str(args.get("kind", "") or "").strip()
        if kind and kind not in ("requirement", "chat"):
            return business_fail_result(
                "framework_prompt", started_ms, f"prompt 的 kind 只能是 'requirement' 或 'chat'（收到 '{kind}'）"
            )
        target_name = str(args.get("agent", "") or "")
        target, failure = self._delegation.resolve_target(target_name, caller, self._roster("receive_prompt"))
        if failure is not None:
            return business_fail_result("framework_prompt", started_ms, failure.message)
        assert target is not None
        outbound = mark_prompt(content, chat=True) if kind == "chat" else content
        delivered = target.receive_prompt(content=outbound, source=caller or "unknown")
        if not delivered:
            return business_fail_result(
                "framework_prompt",
                started_ms,
                f"目标 '{target.name}' 拒收递话（未实现消化通道或留言已满）",
            )
        _emit_prompted(self.event_bus, target=target.name, content=content, source=caller or "unknown")
        return ToolExecutionResult(
            tool_name="framework_prompt",
            success=True,
            structured_content={"delivered": True, "executor": target.name},
        )

    async def _invoke_task_settle(self, args: Dict[str, Any], *, caller: str) -> ToolExecutionResult:
        started_ms = now_ms()
        tool_name = "framework_task_settle"
        settled = await self._delegation.settle(
            target_name=str(args.get("agent", "") or ""),
            item_ref=str(args.get("item", "") or ""),
            outcome=str(args.get("outcome", "") or ""),
            caller=caller,
            progress=str(args.get("progress", "") or ""),
            roster=self._roster("receive_delegation"),
        )
        if settled.ok:
            return ToolExecutionResult(
                tool_name=tool_name,
                success=True,
                structured_content={
                    "settled": True,
                    "outcome": settled.outcome,
                    "status": settled.status,
                    "item": settled.item,
                    "task_id": settled.task_id,
                    "executor": settled.executor,
                },
            )
        if settled.error == "no_ledger":
            return ToolExecutionResult(tool_name=tool_name, success=False, error_message=settled.message)
        return business_fail_result(tool_name, started_ms, settled.message)

    def _invoke_task_status(self, args: Dict[str, Any], *, caller: str) -> ToolExecutionResult:
        started_ms = now_ms()
        if self.task_ledger is None:
            return ToolExecutionResult(
                tool_name="framework_task_status",
                success=False,
                error_message="任务基建未装配（task_ledger 未注入），查状态不可用",
            )
        task_id = str(args.get("task_id", "") or "").strip()
        item_ref = str(args.get("item", "") or "").strip()
        if not task_id and not item_ref:
            return business_fail_result(
                "framework_task_status",
                started_ms,
                "task_status 需要 item（短编号如 '在做'、'待办1'）或 task_id",
            )
        record: Optional[TaskRecord] = None
        label = ""
        if task_id:
            record = self.task_ledger.get(task_id)
        else:
            target, failure = self._delegation.resolve_target(
                str(args.get("agent", "") or ""), caller, self._roster("receive_delegation")
            )
            if failure is not None:
                return business_fail_result("framework_task_status", started_ms, failure.message)
            assert target is not None
            found = find_item(read_board(self.task_ledger, target.name), item_ref)
            if found is not None:
                record = found.record
                label = found.label
        if record is None:
            key = task_id or item_ref
            return business_fail_result(
                "framework_task_status",
                started_ms,
                f"任务不存在: '{key}'（未知编号或已终态移除）",
            )
        payload: Dict[str, Any] = {
            "task_id": record.task_id,
            "status": record.status,
            "initiator": record.initiator,
            "executor": record.executor,
            "snapshot": record.snapshot,
            "updated_at_ms": record.updated_at_ms,
        }
        if label:
            payload["item"] = label
        return ToolExecutionResult(
            tool_name="framework_task_status",
            success=True,
            structured_content=payload,
        )


def build_agent_control_provider(
    manager: AgentManager,
    task_ledger: Optional[TaskLedger] = None,
    event_bus: Optional[Any] = None,
    clock: Optional[Callable[[], int]] = None,
    *,
    delegation_core: Optional[DelegationCore] = None,
) -> AgentControlProvider:
    """工厂：构造 AgentControlProvider；传入 ``delegation_core`` 时与控制面共享同一个受理核心。"""
    return AgentControlProvider(
        manager=manager,
        task_ledger=task_ledger,
        event_bus=event_bus,
        clock=clock,
        delegation_core=delegation_core,
    )


__all__ = [
    "PROVIDER_NAME",
    "DelegationCore",
    "AgentControl",
    "AgentControlProvider",
    "build_agent_control_provider",
]
