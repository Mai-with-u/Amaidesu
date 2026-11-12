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
    def _delivered_delegation(self, executor: str) -> Optional[TaskRecord]:
        """查目标 Agent 名下已送达、未终态的委派单（排队的还没送到，不算）。"""
        if self._task_ledger is None:
            return None
        for task_id in self._task_ledger.active_task_ids():
            record = self._task_ledger.get(task_id)
            if (
                record is not None
                and record.tool == "framework_delegate"
                and record.executor == executor
                and record.status in DELIVERED_TASK_STATES
            ):
                return record
        return None

    @staticmethod
    def _quote_own_words(snapshot: Dict[str, Any]) -> str:
        """取快照里发起方自己的原话（附带的来源对话与取舍规则不外传）。"""
        return str(
            snapshot.get("own_words") or own_words_of(str(snapshot.get("instruction") or "")) or "（快照无指令原文）"
        )

    def _live_queue(self, executor: str) -> List[_QueueEntry]:
        """``executor`` 队列里账面仍在排队的条目（被撤/已了结的不算，顺带清掉）。"""
        queue = self._queues.get(executor)
        if not queue:
            return []
        alive = []
        for entry in queue:
            record = self._task_ledger.get(entry.task_id) if self._task_ledger is not None else None
            if record is not None and record.status == "queued":
                alive.append(entry)
        if len(alive) != len(queue):
            queue.clear()
            queue.extend(alive)
        return alive

    def queued_entries(self, executor: str) -> List[_QueueEntry]:
        """``executor`` 当前排着的委派（队首在前；只读快照）。"""
        return list(self._live_queue(executor))

    def _queue_full_message(self, executor: str, delivered: Optional[TaskRecord], entries: List[_QueueEntry]) -> str:
        """满员拒收理由：在途那件 + 当前队列清单（各条原话与排了多久），并给出路。

        原话只给发起方自己说的那段：每次被拒都把整段来源对话与取舍规则塞回去，
        上千字的旧弹幕会被当成"指令原文"读回来。
        """
        current_ms = now_ms()
        lines = [f"受理失败：目标 '{executor}' 的委派队列已排满 {_DELEGATION_QUEUE_LIMIT} 件，这件没排进去。"]
        if delivered is not None:
            lines.append(self._delivered_line(delivered, current_ms))
        lines.append("排队中（按送达先后）：")
        for index, entry in enumerate(entries, start=1):
            mark = "急件，" if entry.urgent else ""
            waited_s = max(current_ms - entry.enqueued_at_ms, 0) // 1000
            lines.append(f"{index}. 「{entry.own_words}」（{mark}任务号 {entry.task_id}，排了 {waited_s} 秒）")
        lines.append(
            "等前面的做完再派，不要重复派同一件事；不要的那件可以用 framework_task_cancel 撤掉腾位置；"
            "真是急事才用 urgent=true 插队。"
        )
        return "\n".join(lines)

    @staticmethod
    def _delivered_line(record: TaskRecord, current_ms: int) -> str:
        """在途那件的一行说明：原话 + 受理距今 + 已知进展（执行 Agent 最近那句判断不带）。"""
        waited_s = max(current_ms - record.created_at_ms, 0) // 1000
        state = "被急件暂放中" if record.status == "suspended" else "在做"
        line = (
            f"{state}：「{_DelegationCore._quote_own_words(record.snapshot)}」"
            f"（任务号 {record.task_id}，已受理 {waited_s} 秒）"
        )
        # 已知进展摘要有就带（批级关联/执行侧写入快照的额外字段），读不到不硬造。执行 Agent 最近那句判断
        # 不带：那是没脱敏的推理原文加毫秒时刻，会把坐标、工具名念出来
        extras = {
            k: v for k, v in record.snapshot.items() if k not in _NOT_PROGRESS_KEYS and k not in _QUEUE_SNAPSHOT_KEYS
        }
        if extras:
            line += f"；已知进展：{extras}"
        return line

    async def accept(
        self, target: BaseAgent, instruction: str, initiator: str, *, urgent: bool = False
    ) -> _DelegationOutcome:
        """接单：忙闲三分支 → 目标接收入口 → 受理成功登记记录表。

        - 目标闲着（名下没有已送达的在途件、队列也空）：直派，与原路径一致
        - 目标忙 + 普通件：入队（满员拒收附队列清单）
        - 目标忙 + 急件：在途件是普通件且目标暂放成功 → 在途转 suspended、急件立刻派；
          否则急件置顶排队

        受理入口也是看门狗的检查点：每次新的委派活动顺带核对一遍在途单计时。

        直派时任务号先生成再交目标接收（拒收则不登记，不留孤儿 accepted 条目）；
        受理失败（本方法返回的 refusal）与任务失败（执行中写入记录表的 failed
        终态）严格分开——回执只承诺"目标已接收或已排上"，不承诺"能干成"。
        不判新单与在途语义是否相同（必达机制不押 LLM 判断）。framework_prompt 不受队列影响。
        """
        executor = target.name
        delivered = self._delivered_delegation(executor)
        waiting = self._live_queue(executor)
        self._watchdog_check()
        if delivered is None and not waiting:
            return self._deliver_new(target, instruction, initiator, urgent=urgent)
        if urgent:
            if delivered is None:
                # 手上空着、只是队列里还有没送出的：急件直接派，不用等
                return self._deliver_new(target, instruction, initiator, urgent=True)
            preemptable = delivered.status != "suspended" and not delivered.snapshot.get("urgent")
            if preemptable and await self._suspend_current(target, delivered):
                return self._deliver_new(target, instruction, initiator, urgent=True)
            return self._enqueue(target, instruction, initiator, urgent=True)
        if len(waiting) >= _DELEGATION_QUEUE_LIMIT:
            logger.info(f"委派拒受理（队列满 {_DELEGATION_QUEUE_LIMIT} 件）: {initiator} -> {executor}")
            return _DelegationOutcome(
                ok=False, error="refused", message=self._queue_full_message(executor, delivered, waiting)
            )
        outcome = self._enqueue(target, instruction, initiator, urgent=False)
        if delivered is None:
            # 手上空着却有排队件（上一件终态与出队之间的空当）：顺手送出队首
            await self._drain_queue(executor)
        return outcome

    async def _suspend_current(self, target: BaseAgent, delivered: TaskRecord) -> bool:
        """请目标暂放手头的活（可选协议 ``suspend_current``）；成功则在途单记 suspended。"""
        try:
            suspended = bool(await target.suspend_current())
        except Exception as exc:  # noqa: BLE001 - 暂放失败按不支持处理，急件降级置顶排队
            logger.warning(f"目标 '{target.name}' 暂放手头的活失败，急件改为置顶排队: {exc}", exc=True)
            return False
        if not suspended:
            logger.info(f"目标 '{target.name}' 不支持或没能暂放手头的活，急件改为置顶排队")
            return False
        self._task_ledger.update(
            delivered.task_id, "suspended", summary="有急事插队，这件先暂放（进度保留），急事做完接着做"
        )
        logger.info(f"急件插队：'{target.name}' 手头的 {delivered.task_id} 已暂放")
        return True

    def _deliver_new(self, target: BaseAgent, instruction: str, initiator: str, *, urgent: bool) -> _DelegationOutcome:
        """直派一件新委派：生成任务号 → 目标接收入口 → 受理成功登记记录表。"""
        executor = target.name
        task_id = _generate_task_id()
        receive = getattr(target, "receive_delegation", None)
        if not callable(receive):
            return _DelegationOutcome(
                ok=False, error="refused", message=f"受理失败：目标 '{executor}' 未实现接收入口（默认拒收）"
            )
        rejected = receive(instruction=instruction, task_id=task_id)
        if rejected:
            logger.info(f"delegate: Agent '{executor}' 拒收委派（{rejected}）")
            return _DelegationOutcome(
                ok=False, error="refused", message=f"受理失败：目标 '{executor}' 拒收（{rejected}）"
            )
        # 受理成功：登记 agent 型任务（执行 Agent 唯一写入；循环不代写）。
        # 账面存指令全文（游戏侧与看板要用，截过的半句对不上），另记发起方自己的原话：
        # 拒受理理由回给发起方、落库留给下播核对与续播认领的都只用原话，附带的来源对话不外传
        self._task_ledger.register(
            task_id=task_id,
            provider="framework",
            tool="framework_delegate",
            initiator=initiator,
            executor=executor,
            status="accepted",
            source="agent",
            snapshot=self._snapshot(instruction, urgent=urgent),
        )
        logger.info(f"委派受理: {initiator} -> {executor}（task_id={task_id}{'，急件' if urgent else ''}）")
        self._watchdog_start(task_id)
        return _DelegationOutcome(ok=True, task_id=task_id, executor=executor)

    def _enqueue(self, target: BaseAgent, instruction: str, initiator: str, *, urgent: bool) -> _DelegationOutcome:
        """排进目标的委派队列：急件置顶、普通件排尾；账面先登记受理再转 queued。

        先以 accepted 登记：落库据此记下受理行（下播核对与重启收尾才认得这件——重启时
        还排着的单与在途单同样被标成被打断），随后转 queued 广播一次排队。
        """
        executor = target.name
        task_id = _generate_task_id()
        entry = _QueueEntry(
            task_id=task_id,
            instruction=instruction,
            own_words=own_words_of(instruction),
            initiator=initiator,
            urgent=urgent,
            enqueued_at_ms=now_ms(),
        )
        queue = self._queues.setdefault(executor, deque())
        if urgent:
            # 急件置顶，但排在已有急件之后：急件之间仍按先来后到
            index = sum(1 for item in queue if item.urgent)
            queue.insert(index, entry)
        else:
            queue.append(entry)
        position = list(queue).index(entry) + 1
        snapshot = self._snapshot(instruction, urgent=urgent)
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
        self._task_ledger.update(
            task_id,
            "queued",
            summary=f"目标手上有事，排在委派队列第 {position} 位{'（急件置顶）' if urgent else ''}",
            snapshot={**snapshot, "queued_at_ms": entry.enqueued_at_ms},
        )
        logger.info(
            f"委派入队: {initiator} -> {executor}（task_id={task_id}，第 {position} 位{'，急件' if urgent else ''}）"
        )
        return _DelegationOutcome(ok=True, task_id=task_id, executor=executor, queued=True, queue_position=position)

    @staticmethod
    def _snapshot(instruction: str, *, urgent: bool) -> Dict[str, Any]:
        """受理快照：指令全文 + 发起方原话；急件另记标记（不嵌套挂起的判据）。"""
        snapshot: Dict[str, Any] = {"instruction": instruction, "own_words": own_words_of(instruction)}
        if urgent:
            snapshot["urgent"] = True
        return snapshot

    def cancel_queued(self, task_id: str, *, source: str = "") -> bool:
        """撤一件还在排队、没送到执行方的委派：出队 + 账面 cancelled。

        排队件执行方从没见过，撤它不经执行 Agent；已送达的单仍走执行 Agent 的
        取消入口（单写者规则）。账面不是 queued 返回 False。别的入口登记的排队件
        也能撤：账面转 cancelled 后，持有它的队列出队时会跳过。
        """
        if self._task_ledger is None:
            return False
        record = self._task_ledger.get(task_id)
        if record is None or record.status != "queued":
            return False
        queue = self._queues.get(record.executor)
        if queue:
            remaining = [entry for entry in queue if entry.task_id != task_id]
            queue.clear()
            queue.extend(remaining)
        self._task_ledger.update(
            task_id, "cancelled", summary=f"排队中被撤（还没送到执行方，source={source or '未知'}）"
        )
        logger.info(f"委派排队件被撤: {task_id}（source={source or '未知'}）")
        return True

    async def delegate(
        self,
        *,
        target_name: str,
        instruction: str,
        caller: str,
        roster: Optional[Dict[str, str]] = None,
        urgent: bool = False,
    ) -> _DelegationOutcome:
        """受理一次委派（两条入口的完整前置序列）：基建在位 → 解析目标 → 接单。"""
        if self._task_ledger is None:
            return _DelegationOutcome(
                ok=False, error="no_ledger", message="任务基建未装配（task_ledger 未注入），委派不可用"
            )
        target, failure = self.resolve_target(target_name, caller, roster)
        if failure is not None:
            return failure
        assert target is not None  # 解析成功时必有实例（类型收窄）
        return await self.accept(target, instruction, caller or "unknown", urgent=urgent)


class AgentControl:
    """框架级控制——直接调用接口（不进 ToolRegistry）。"""

    def __init__(
        self,
        manager: AgentManager,
        task_ledger: Optional[TaskLedger] = None,
        event_bus: Optional[Any] = None,
        clock: Optional[Callable[[], int]] = None,
    ) -> None:
        self._manager = manager
        self._task_ledger = task_ledger
        self._event_bus = event_bus
        self._delegation = _DelegationCore(manager, task_ledger, event_bus, clock)

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
        """向指定 Agent 递话（控制面直调，供运营 REST）。

        返回 ``{"ok": True, "delivered": True, "executor": name}`` 或
        ``{"ok": False, "error": "not_found"|"refused", "message": …}``——
        两种失败语义分开，REST 侧据此映射 404 / 409。
        """
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

        取消经执行 Agent（单写者规则：框架不代写账面）；``reason`` 非空时
        随停手通知原文转给执行 Agent。返回
        ``{"ok": True, "cancelled": True}`` 或
        ``{"ok": False, "error": "agent_not_found"|"task_not_found",
        "message": …}``——REST 侧一律映射 404（不在名册 / 任务未知或已终态）。
        """
        # 还在排队、没送到执行方的单不经执行 Agent（它从没见过）：队列直接撤
        if self._delegation.cancel_queued(task_id, source=source):
            return {"ok": True, "cancelled": True}
        agent = self._manager.get_agent_by_name(name)
        if agent is None:
            logger.warning(f"cancel_task: 未找到 Agent '{name}'")
            return {"ok": False, "error": "agent_not_found", "message": f"Agent 不存在: {name}"}
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
        """急停指定 Agent（控制面直调，供运营 REST）：停掉所有在跑目标并注入叫停理由。

        默认只停手不撤委派单；``cancel_delegations`` 为真时连委派单一起撤
        （复用执行 Agent 的 cancel 路径，单写者规则不动）。返回
        ``{"ok": True, "stopped": True}`` 或
        ``{"ok": False, "error": "agent_not_found"|"not_supported", "message": …}``。
        """
        agent = self._manager.get_agent_by_name(name)
        if agent is None:
            logger.warning(f"stop_actions: 未找到 Agent '{name}'")
            return {"ok": False, "error": "agent_not_found", "message": f"Agent 不存在: {name}"}
        stopped = await agent.stop_actions(reason, cancel_delegations=cancel_delegations)
        if not stopped:
            logger.info(f"stop_actions: Agent '{name}' 不支持急停")
            return {"ok": False, "error": "not_supported", "message": f"Agent '{name}' 不支持急停"}
        return {"ok": True, "stopped": True}

    async def delegate(self, name: str, instruction: str, *, initiator: str = "operator", urgent: bool = False) -> dict:
        """运营直派任务（控制面直调，供 REST）：委托给受理核心，映射 REST 错误形态。

        受理前置（目标存在、受理分支、接收入口、账面登记）全部在
        ``_DelegationCore``——与 LLM 工具委派同一套不变量、同一张任务记录表
        （source="agent"），发起方记 ``initiator``（REST 侧固定 "operator"）。
        返回 ``{"ok": True, "accepted": True, "task_id": …, "executor": …}`` 或
        ``{"ok": False, "error": "not_found"|"refused"|"no_ledger", "message": …}``——
        REST 侧据此映射 404 / 409 / 503；排队复用受理成功形态、满员拒收复用 refused（409）。
        """
        outcome = await self._delegation.delegate(
            target_name=name, instruction=instruction, caller=initiator, urgent=urgent
        )
        if outcome.ok:
            receipt: dict = {"ok": True, "accepted": True, "task_id": outcome.task_id, "executor": outcome.executor}
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
        # is_alive 走 manager 的守护阈值（config 化）；能走到这里说明 agent 在名册，不会是 None
        alive = self._manager.is_agent_alive(name)
        return {
            "name": agent.name,
            "state": agent.state.value,
            "heartbeat_ms": agent.heartbeat.last_heartbeat_ms,
            "is_alive": alive if alive is not None else agent.is_alive(),
            "restart_count": agent.restart_count,
        }


# -------------------- Provider：用于注册进 ToolRegistry --------------------


@dataclass(slots=True)
class AgentControlProvider(BaseToolProvider):
    """把 framework LLM 工具面（委派/递话/任务状态）注册到 ToolRegistry 的 Provider。

    控制工具（pause/resume/shutdown/restart/list/state）不在本 provider 中——
    控制面由 DashboardServer 经 API 直调 ``AgentControl`` 类本体。

    委派原语：
    - ``framework_delegate(agent, instruction)``：名册校验 + **禁自派** +
      目标接收入口（BaseAgent 默认拒收）→ 受理成功登记同一张任务记录表
      （agent 型：发起方=调用方、执行者=目标、事实源=执行 Agent）
    - 受理失败（目标不存在/未启用/拒收/自派/队列满员）与任务失败（执行中失败）分开
    - ``framework_task_status(task_id)``：查记录表；未知任务号 → 失败结果
    - ``framework_task_cancel(task_id)``：撤委派单——只收 deleg_ 前缀的框架
      任务号，转发目标 Agent 的 ``cancel_task``（单写者规则：取消经执行 Agent，
      框架不代写账面）；执行方内部目标号（如 maicraft-goal-N）拒绝并提示走
      执行方自己的目标工具收场

    递话原语（与委派并列：传输同路、记账分家——纯文本留言不进账本）：
    - ``framework_prompt(agent, content)``：目标解析与禁自派复用委派同款
      逻辑；调 ``target.receive_prompt``，拒收 → 失败结果、受理 → 回执
      （无任务号）
    """

    manager: AgentManager
    task_ledger: Optional[TaskLedger] = None
    event_bus: Optional[Any] = None
    clock: Optional[Callable[[], int]] = None  # 保留给测试注入假时钟（委派看门狗计时用）
    _delegation: _DelegationCore = field(init=False, repr=False)  # 委派受理核心（与控制面直调共用）

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category: ClassVar[str] = "framework"

    def __post_init__(self) -> None:
        self._delegation = _DelegationCore(self.manager, self.task_ledger, self.event_bus, self.clock)

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    def list_tools(self) -> Iterable[ToolSpec]:
        """委派 + 递话 + 任务状态 + 任务撤销工具（恒四件）。

        **参数枚举里的名册只含真正覆写了接收入口的 Agent**——没接入的 Agent
        （如纯手柄型游戏 Agent）不出现在 enum 与描述里，LLM 从源头选不出
        "必被拒收"的委派目标。四件必须恒返回：registry 的 invoke 表是注册期
        快照（装配顺序总是 framework 先注册、Agent 后注册），此刻名册常为空，
        按名册裁剪工具会把 delegate 砍出 invoke 表、命令直连链路整体断掉。
        接入状态变化经重启后下一次装配生效（与名册快照同节拍）。
        """
        return [self._delegate_spec(), self._prompt_spec(), _TASK_STATUS_SPEC, _TASK_CANCEL_SPEC]

    def _roster(self, hook: str) -> Dict[str, str]:
        """当前名册 {注册名: 描述}，仅含覆写了 ``hook`` 接收入口的 Agent。

        能力探测，覆写即声明（单一事实源）：``BaseAgent.receive_delegation`` /
        ``receive_prompt`` 默认拒收，子类覆写才代表真正接入。未覆写的 Agent
        不进名册——委派/递话工具的参数枚举里看不到它，从源头杜绝"选了必被
        拒收的目标"的无效调用（调用侧 ``_DelegationCore.resolve_target`` 的
        拒收校验保留，防编名直调）。manager 不可达或缺查询接口时按空名册。
        """
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
                continue  # 未覆写：默认拒收，不进名册
            roster[name] = str(descriptions.get(name, "") or "")
        return roster

    def _agent_target_description(self, roster: Dict[str, str]) -> str:
        """构造 agent 参数的动态说明（名册 + 描述；委派/递话共用）。"""
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
        """framework 工具面的使用说明：有能接收委派的 Agent 才给，否则空串。

        读说明的 Agent 自己不算候选（禁自派）。说明只点明"可以委派给谁"
        与三件工具的分工；委派型游戏的完整守则由读说明的一方按自己的提示词
        追加——框架不写主播口吻的话。
        """
        roster = {name: desc for name, desc in self._roster("receive_delegation").items() if name != agent_name}
        if not roster:
            return ""
        listed = "；".join(f"{name}={desc or '（无描述）'}" for name, desc in sorted(roster.items()))
        return (
            f"委派型游戏（可以把一件事交给它在后台做）：{listed}。"
            "用 framework_delegate 开始做一件事（只给目标与约束，不给步骤），"
            "用 framework_prompt 给手上正在做的事补充要求；"
            "目标手上有事没做完时普通件自动排队（回执带排位），急事才把 urgent 设为 true 插队；"
            "用 framework_task_cancel 报委派任务号可以撤掉在途或排队的；"
            "进度以它的上报与 framework_task_status 的查询结果为准，不要自己推测。"
        )

    def _delegate_spec(self) -> ToolSpec:
        """构造委派规格：把名册（注册名 + 描述）写进 agent 参数说明与枚举。"""
        roster = self._roster("receive_delegation")
        schema: Dict[str, Any] = {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": self._agent_target_description(roster)},
                "instruction": {
                    "type": "string",
                    "description": "工作指令（自然语言：目标与约束，不规定步骤与次序）",
                },
                "urgent": {
                    "type": "boolean",
                    "description": (
                        "是否急件（默认 false）。目标忙时：true 且确属急事才插队——目标会暂放手头的活"
                        "立刻办这件（手头那件保留进度，办完接着做）；普通排队的事不要设 true。"
                    ),
                },
            },
            "required": ["instruction"],
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
        """构造递话规格：agent 参数说明与枚举随可递话名册动态构造。"""
        roster = self._roster("receive_prompt")
        schema: Dict[str, Any] = {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": self._agent_target_description(roster)},
                "content": {"type": "string", "description": "递话内容（自然语言：想说给目标的话）"},
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
        # 调用方使用的就是派生全名（framework_delegate 等），等值对照分发
        try:
            if name == "framework_delegate":
                return await self._invoke_delegate(args, caller=invocation.source)
            if name == "framework_prompt":
                return self._invoke_prompt(args, caller=invocation.source)
            if name == "framework_task_status":
                return self._invoke_task_status(args)
            if name == "framework_task_cancel":
                return await self._invoke_task_cancel(args, caller=invocation.source)
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

    # ----- 委派原语（framework_delegate / framework_task_status） -----

    async def _invoke_delegate(self, args: Dict[str, Any], *, caller: str) -> ToolExecutionResult:
        """受理委派：指令校验后委托受理核心，把统一结果映射成工具失败/回执。

        目标解析、受理分支、接收入口、账面登记都在 ``_DelegationCore``——
        与控制面直调（REST）共用同一套受理不变量。

        失败分两类：目标不存在/在途冲突/目标拒收这类"对方或调用方纠正后能重派"的
        业务拒绝，标 ``failure_kind="business"``（不算执行故障，不进熔断计数）；
        任务基建未装配这类依赖不可用是真执行故障，不标——标了健康监控就废了。
        """
        started_ms = now_ms()
        instruction = str(args.get("instruction", "") or "")
        if not instruction:
            return business_fail_result("framework_delegate", started_ms, "delegate 需要 instruction（自然语言指令）")
        outcome = await self._delegation.delegate(
            target_name=str(args.get("agent", "") or ""),
            instruction=instruction,
            caller=caller,
            roster=self._roster("receive_delegation"),
            urgent=bool(args.get("urgent", False)),
        )
        if outcome.ok:
            receipt: Dict[str, Any] = {"accepted": True, "task_id": outcome.task_id, "executor": outcome.executor}
            if outcome.queued:
                receipt["queued"] = True
                receipt["queue_position"] = outcome.queue_position
                receipt["note"] = "目标手上有事，这件排进了委派队列，轮到时会送达；不要重复派。"
            return ToolExecutionResult(
                tool_name="framework_delegate",
                success=True,
                structured_content=receipt,
            )
        if outcome.error == "no_ledger":
            # 任务基建未装配：依赖不可用，重试无用——真执行故障，不标业务拒绝。
            return ToolExecutionResult(
                tool_name="framework_delegate",
                success=False,
                error_message=outcome.message,
            )
        return business_fail_result("framework_delegate", started_ms, outcome.message)

    def _invoke_prompt(self, args: Dict[str, Any], *, caller: str) -> ToolExecutionResult:
        """受理递话：目标解析（复用受理核心，委派同款不变量）→ 目标接收入口 → 回执。

        纯文本留言**不登记任务记录表**——递话与委派传输同路、记账分家。

        失败都是对方或调用方纠正后能重来的业务拒绝（内容为空、目标不存在或自派、
        目标拒收递话），标 ``failure_kind="business"``——不算执行故障，不进熔断计数。
        """
        started_ms = now_ms()
        content = str(args.get("content", "") or "")
        if not content:
            return business_fail_result("framework_prompt", started_ms, "prompt 需要 content（递话文本）")
        target_name = str(args.get("agent", "") or "")
        target, failure = self._delegation.resolve_target(target_name, caller, self._roster("receive_prompt"))
        if failure is not None:
            return business_fail_result("framework_prompt", started_ms, failure.message)
        assert target is not None  # 解析成功时必有实例（类型收窄）
        delivered = target.receive_prompt(content=content, source=caller or "unknown")
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

    async def _invoke_task_cancel(self, args: Dict[str, Any], *, caller: str) -> ToolExecutionResult:
        """撤委派单：前缀校验 → 查记录表定位执行者 → 转发目标 cancel_task_now。

        只收 deleg_ 前缀的框架委派号——执行方内部目标号（如 maicraft-goal-N）
        是另一本账（执行 Agent 在游戏里挂的目标），不归框架撤，提示走执行方
        自己的目标工具。取消经执行 Agent（单写者规则），框架不代写账面。

        失败多数是业务拒绝（任务号写错、任务已终态、执行者不在名册）——调用方
        换个号或等一会儿就能重来，标 ``failure_kind="business"``，不进熔断计数；
        任务基建未装配是依赖不可用，属真执行故障，不标。
        """
        started_ms = now_ms()
        task_id = str(args.get("task_id", "") or "")
        tool_name = "framework_task_cancel"
        if not task_id:
            return business_fail_result(
                tool_name,
                started_ms,
                "task_cancel 需要 task_id（framework_delegate 回执返回的委派任务号）",
            )
        if not task_id.startswith("deleg_"):
            return business_fail_result(
                tool_name,
                started_ms,
                (
                    f"只能撤委派任务（deleg_ 开头），'{task_id}' 是执行方内部目标号，"
                    "不归这里撤——让执行方用它自己的目标工具（如 maicraft_goal 的 cancel）收场"
                ),
            )
        if self.task_ledger is None:
            # 任务基建未装配：依赖不可用，重试无用——真执行故障，不标业务拒绝。
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error_message="任务基建未装配（task_ledger 未注入），撤单不可用",
            )
        record = self.task_ledger.get(task_id)
        if record is None:
            return business_fail_result(tool_name, started_ms, f"任务不存在: '{task_id}'（未知任务号或已终态）")
        if record.status == "queued" and self._delegation.cancel_queued(task_id, source=caller or "unknown"):
            return ToolExecutionResult(
                tool_name=tool_name,
                success=True,
                structured_content={"cancelled": True, "task_id": task_id, "executor": record.executor},
            )
        target = self.manager.get_agent_by_name(record.executor)
        if target is None:
            logger.warning(f"task_cancel: 执行者 '{record.executor}' 不在名册（task_id={task_id}）")
            return business_fail_result(
                tool_name,
                started_ms,
                f"撤单失败：执行者 '{record.executor}' 不在名册，无法送达停手通知",
            )
        cancelled = await target.cancel_task_now(task_id, source=caller or "unknown")
        if not cancelled:
            return business_fail_result(
                tool_name,
                started_ms,
                f"撤单失败：任务 '{task_id}' 不在执行者 '{record.executor}' 的追踪范围（未知号或已终态）",
            )
        logger.info(f"委派撤单: {caller or 'unknown'} 撤掉 {record.executor} 的 {task_id}")
        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            structured_content={"cancelled": True, "task_id": task_id, "executor": record.executor},
        )

    def _invoke_task_status(self, args: Dict[str, Any]) -> ToolExecutionResult:
        """按任务号查记录表：状态 + 快照；未知/已终态移除 → 失败结果。

        查不到任务是业务拒绝（任务号写错或已终态），标 ``failure_kind="business"``；
        任务基建未装配是依赖不可用，属真执行故障，不标。
        """
        started_ms = now_ms()
        task_id = str(args.get("task_id", "") or "")
        if not task_id:
            return business_fail_result(
                "framework_task_status",
                started_ms,
                "task_status 需要 task_id（framework_delegate 回执返回的委派任务号）",
            )
        if self.task_ledger is None:
            # 任务基建未装配：依赖不可用，重试无用——真执行故障，不标业务拒绝。
            return ToolExecutionResult(
                tool_name="framework_task_status",
                success=False,
                error_message="任务基建未装配（task_ledger 未注入），查状态不可用",
            )
        record = self.task_ledger.get(task_id)
        if record is None:
            return business_fail_result(
                "framework_task_status",
                started_ms,
                f"任务不存在: '{task_id}'（未知任务号或已终态移除）",
            )
        return ToolExecutionResult(
            tool_name="framework_task_status",
            success=True,
            structured_content={
                "task_id": record.task_id,
                "status": record.status,
                "initiator": record.initiator,
                "executor": record.executor,
                "snapshot": record.snapshot,
                "updated_at_ms": record.updated_at_ms,
            },
        )


def build_agent_control_provider(
    manager: AgentManager,
    task_ledger: Optional[TaskLedger] = None,
    event_bus: Optional[Any] = None,
    clock: Optional[Callable[[], int]] = None,
) -> AgentControlProvider:
    """工厂：构造一个 AgentControlProvider 绑定 manager（+ 任务记录表供委派）。"""
    return AgentControlProvider(manager=manager, task_ledger=task_ledger, event_bus=event_bus, clock=clock)


__all__ = [
    "PROVIDER_NAME",
    "AgentControl",
    "AgentControlProvider",
    "build_agent_control_provider",
]
