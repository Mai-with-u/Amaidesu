"""
AgentControl —— 框架级 Agent 控制与委派/递话

- framework_delegate / framework_task_status / framework_task_cancel：
  跨 Agent 委派原语——派活拿回执（accepted + task_id），任务进度随时可查，
  在途可撤；指令只当自然语言（给目标，不给步骤），不加编排/条件分支
- framework_prompt：递话原语——纯文本留言（插话/提醒），不派任务、
  不进账本；与委派共享 Agent 侧文本接缝（receive_prompt），记账分家
- provider="framework"（框架内置提供，非独立源；可见名单默认 ["*"]）
- pause / resume / shutdown / 状态查询等控制能力由 ``AgentControl``
  类本体承载，不进 LLM 工具面；控制面（DashboardServer）经 API 直调
  （重建走 ``AgentManager.rebuild``，不经本类）

LLM 工具面注册方式（framework provider 含 delegate/prompt/task_status/task_cancel 四个 spec）：
```python
provider = build_agent_control_provider(manager, task_ledger)
tool_registry.register_provider(provider)
```
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from collections import deque
from typing import Any, ClassVar, Deque, Dict, Iterable, List, Optional

from src.modules.agents.base import BaseAgent
from src.modules.agents.game_tools import business_fail_result
from src.modules.agents.manager import AgentManager
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.agents import AgentPromptedPayload
from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.agents.delegation_text import own_words_of
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.tools.tasks import (
    DELIVERED_TASK_STATES,
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

# 委派队列深度上限（每个执行者一条队列）：超出后普通件拒收并附当前队列清单
_DELEGATION_QUEUE_LIMIT = 5

# 拒受理/队列清单里不当"已知进展"列出的快照字段：指令全文与原话另有一行；执行 Agent 最近那句判断是没脱敏的
# 推理原文，不给发起方回读
_NOT_PROGRESS_KEYS = frozenset({"instruction", "own_words", REPLY_NOTE_KEY, REPLY_NOTE_AT_KEY})
# 委派队列自己写进快照的标记（急件、入队时刻）：不是执行进展，也不进拒收理由
_QUEUE_SNAPSHOT_KEYS = frozenset({"urgent", "queued_at_ms"})


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


# -------------------- 工具规格 --------------------

#: 委派工具的固定说明（可委派对象随名册动态追加，见 AgentControlProvider.list_tools）
_DELEGATE_DESCRIPTION = (
    "把一项工作委派给目标 Agent：给目标与自然语言指令（不给步骤），"
    "立刻返回受理回执（accepted + task_id）。任务状态变化会以事件通知你；"
    "随时可用 framework_task_status 按 task_id 查询进度与快照。"
    "目标手上已有未完成的事时普通件自动排队（回执带排位），不用重复派、"
    "也不用先撤在途；确属急事才把 urgent 设为 true：目标支持暂放时会放下手头的活"
    "立刻办你的急事（手头那件挂起保留、急事做完接着做），不支持时急件置顶排队。"
    "同一时刻最多一件急事在办，第二件急件也置顶排队。"
)

#: 递话工具的固定说明
_PROMPT_DESCRIPTION = (
    "向目标 Agent 递话（插话/提醒/纠正）：纯文本留言，不派新任务、不产生"
    "任务号。目标执行中会在下一步推理前吸收，任务挂起中会被唤醒重新判断。"
)

#: 任务状态工具规格
_TASK_STATUS_SPEC: ToolSpec = ToolSpec(
    name="task_status",
    description=(
        "按任务号查询异步任务（委派 / 回执型工具受理）的当前状态与快照。"
        "查询玩家工作文档（todo/notebook/近期上报）用各游戏 Agent 的读取工具，"
        "两者不重复。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "受理回执返回的任务号"},
        },
        "required": ["task_id"],
    },
    kind="sync",
    provider=PROVIDER_NAME,
)

#: 任务撤销工具规格（与 task_status 对仗；只收框架委派号）
_TASK_CANCEL_SPEC: ToolSpec = ToolSpec(
    name="task_cancel",
    description=(
        "撤掉一个还在途的委派任务：按任务号叫停，目标会收到停手通知。"
        "只收 framework_delegate 受理回执里的委派任务号（deleg_ 开头）；"
        "执行方自己在游戏里挂的目标不归它管，由执行方用自己的目标工具收场。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "要撤掉的委派任务号（deleg_ 开头）"},
        },
        "required": ["task_id"],
    },
    kind="sync",
    provider=PROVIDER_NAME,
)


# -------------------- AgentControl 工具（写形式，可直接 await） --------------------

# 任务号序号源（模块级共享：provider 与控制面直调同一序列，避免同毫秒撞号）
_task_id_seq = 0


def _generate_task_id() -> str:
    """生成委派任务号（deleg_{epoch_ms}_{seq}，全链路关联键）。"""
    global _task_id_seq
    _task_id_seq += 1
    return f"deleg_{now_ms()}_{_task_id_seq}"


# -------------------- 委派受理核心（两条入口共用） --------------------


@dataclass(slots=True)
class _DelegationOutcome:
    """委派受理结果——受理核心的统一产出，失败形态由各入口适配器自行映射。

    ``error`` 取值：not_found（目标不在名册/无候选）/ refused（自派、拒收、
    满员、未接入）/ no_ledger（任务基建未装配）；工具面只消费 message，
    控制面把 error 原样交给 REST 侧映射 404 / 409 / 503。
    ``queued`` 为真表示目标忙、这件进了委派队列（``queue_position`` 从 1 起），
    受理回执照常给任务号——排队也是受理。
    """

    ok: bool
    error: str = ""
    message: str = ""
    task_id: str = ""
    executor: str = ""
    queued: bool = False
    queue_position: int = 0


@dataclass(slots=True)
class _QueueEntry:
    """委派队列条目：受理了还没送到执行方的那件（队列是 Amaidesu 侧机制，零 Mod 依赖）。

    ``own_words`` 是发起方自己的原话（拒收理由与队列清单回显用它，附带的来源
    对话不外传）；``urgent`` 为真表示急件（插队/置顶，不受深度上限约束）。
    """

    task_id: str
    instruction: str
    own_words: str
    initiator: str
    urgent: bool
    enqueued_at_ms: int


class _DelegationCore:
    """委派受理核心：目标解析 → 忙闲三分支 → 目标接收入口 → 账面登记。

    LLM 工具面（``AgentControlProvider``）与控制面直调（``AgentControl.delegate``）
    两条入口共用同一套受理不变量——目标存在、禁自派、任务号同序列、账面条目同形
    （source="agent"、tool="framework_delegate"）；本类只产受理结果，不关心调用方
    是 LLM 还是运营。指令非空由调用方入口自行校验（provider 校验后给 LLM 可读理由；
    REST 的请求体契约不在此收紧）。

    受理三分支：目标闲着直派（原路径不动）；目标忙 + 普通件入队（上限
    ``_DELEGATION_QUEUE_LIMIT``，满员拒收附队列清单）；目标忙 + 急件——先试
    ``target.suspend_current()``（BaseAgent 可选协议；实现了的执行方暂放手头的活、
    进度保留），成功就在途单转 suspended、急件立刻派，未实现或暂放失败降级为
    急件置顶排队。在途单已是急件时不再嵌套挂起，第二件急件同样置顶排队
    （同一时刻最多一件急事在办）。排队的单在目标名下在途件走到终态时按序送达
    （订阅 task.changed 触发）；送达被目标拒收当场转 failed 带原因，不留孤儿条目。
    """

    def __init__(
        self, manager: AgentManager, task_ledger: Optional[TaskLedger], event_bus: Optional[Any] = None
    ) -> None:
        self._manager = manager
        self._task_ledger = task_ledger
        # 每个执行者一条队列（注册名 → 排队条目，队首先送达）
        self._queues: Dict[str, Deque[_QueueEntry]] = {}
        if event_bus is not None and task_ledger is not None:
            event_bus.on(CoreEvents.TASK_CHANGED, self._on_task_changed, model_class=TaskChangedPayload)

    async def _on_task_changed(self, event_name: str, payload: TaskChangedPayload, source: str) -> None:
        """task.changed 回调：目标名下在途件走到终态时，把排队的下一件送达。"""
        del event_name, source
        if payload.source != "agent" or payload.status not in TERMINAL_TASK_STATES:
            return
        await self._drain_queue(payload.executor)

    async def _drain_queue(self, executor: str) -> None:
        """按序送达 ``executor`` 的排队件：队首已不在账（被撤/终态）就跳过；
        目标还有非排队在途件（刚被恢复的挂起件、别的急件）就不送。"""
        queue = self._queues.get(executor)
        if not queue:
            return
        while queue:
            head = queue[0]
            record = self._task_ledger.get(head.task_id) if self._task_ledger is not None else None
            if record is None or record.status != "queued":
                # 账面已了结（下播结算、运营撤单）：队列条目跟着出队，不再送达
                queue.popleft()
                continue
            if self._delivered_delegation(executor) is not None:
                return  # 手上还有正在办的事（如刚恢复的挂起件），排队件继续等
            queue.popleft()
            target = self._manager.get_agent_by_name(executor)
            if target is None:
                # 执行者不在名册（被停用/重建中）：当场转 failed，不留排队孤儿
                self._task_ledger.update(head.task_id, "failed", summary=f"送达失败：执行者 '{executor}' 不在名册")
                continue
            await self._deliver_entry(target, head)
            return  # 一次终态只送一件；这件终态时下一件会再被触发

    async def _deliver_entry(self, target: BaseAgent, entry: _QueueEntry) -> _DelegationOutcome:
        """把排队那件送达执行方：拒收当场转 failed 带原因（不孤儿），接了转回 accepted。"""
        receive = getattr(target, "receive_delegation", None)
        rejected = (
            receive(instruction=entry.instruction, task_id=entry.task_id) if callable(receive) else "目标未实现接收入口"
        )
        if rejected:
            logger.info(f"排队件送达被拒（task_id={entry.task_id}, executor={target.name}）：{rejected}")
            self._task_ledger.update(entry.task_id, "failed", summary=f"送达被目标拒收：{rejected}", snapshot=None)
            return _DelegationOutcome(
                ok=False, error="refused", message=f"受理失败：目标 '{target.name}' 拒收（{rejected}）"
            )
        self._task_ledger.update(entry.task_id, "accepted", summary="轮到执行，已送达目标")
        logger.info(f"排队件送达: {entry.initiator} -> {target.name}（task_id={entry.task_id}）")
        return _DelegationOutcome(ok=True, task_id=entry.task_id, executor=target.name)

    def resolve_target(
        self, target_name: str, caller: str, roster: Optional[Dict[str, str]]
    ) -> tuple[Optional[BaseAgent], Optional[_DelegationOutcome]]:
        """解析目标 Agent（委派/递话共用）：留空兜底 + 禁自派 + 名册校验。

        留空目标按**当前唯一能接收对应入口的 Agent** 解析：候选名单由调用方
        按 ``roster``（覆写了接收入口的名册）传入，接的是哪个游戏由配置的
        enabled 名单决定，这里不写死任何游戏名；候选不唯一就拒绝并要求点名，
        避免把话递错游戏。控制面直调总是显式点名，``roster`` 传 None 即可。
        解析失败返回 ``(None, failure)``。
        """
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

        直派时任务号先生成再交目标接收（拒收则不登记，不留孤儿 accepted 条目）；
        受理失败（本方法返回的 refusal）与任务失败（执行中写入记录表的 failed
        终态）严格分开——回执只承诺"目标已接收或已排上"，不承诺"能干成"。
        不判新单与在途语义是否相同（必达机制不押 LLM 判断）。framework_prompt 不受队列影响。
        """
        executor = target.name
        delivered = self._delivered_delegation(executor)
        waiting = self._live_queue(executor)
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
    ) -> None:
        self._manager = manager
        self._task_ledger = task_ledger
        self._event_bus = event_bus
        self._delegation = _DelegationCore(manager, task_ledger, event_bus)

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
    _delegation: _DelegationCore = field(init=False, repr=False)  # 委派受理核心（与控制面直调共用）

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category: ClassVar[str] = "framework"

    def __post_init__(self) -> None:
        self._delegation = _DelegationCore(self.manager, self.task_ledger, self.event_bus)

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
) -> AgentControlProvider:
    """工厂：构造一个 AgentControlProvider 绑定 manager（+ 任务记录表供委派）。"""
    return AgentControlProvider(manager=manager, task_ledger=task_ledger, event_bus=event_bus)


__all__ = [
    "PROVIDER_NAME",
    "AgentControl",
    "AgentControlProvider",
    "build_agent_control_provider",
]
