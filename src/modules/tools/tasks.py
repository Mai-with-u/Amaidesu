"""
异步任务基建（回执型工具的受理-跟踪-通知，ADR-013）

适用对象：调用拿到的是**任务号**而非结果的工具（如 maicraft_execute）与
跨 Agent 委派。六件套：

1. 受理约定：结构化结果 ``accepted=true + task_id``（回合不阻塞）
2. 任务记录表（``TaskLedger``）：内存 dict；终态移除条目
3. 查询适配器（``BaseToolProvider.query_task`` 可选钩子）：向事实源查询
   任务快照（MCP = 调 server 的任务查询工具）
4. 通知适配器（``BaseToolProvider.subscribe_task_notifications`` 可选钩子）：
   订阅执行侧的通知（通知只是提示、查询才是事实源）
5. 跟踪循环（``TaskTracker``，照 ``ToolHealthMonitor`` 形态：后台循环 +
   可测单步 ``step()``）：周期兜底 + 通知举旗双通道；订阅起停归它
6. 事件与唤醒：``task.changed``（仅状态真变化时发）→ BaseAgent 默认按
   ``payload.initiator == self.name`` 过滤唤醒

单写者规则（按事实源定）：
- **provider 型**（真相在外部系统，如 maicraft）：跟踪循环唯一写入，
  执行 Agent 只被通知
- **agent 型**（委派，真相 = 执行 Agent 的状态）：执行 Agent 唯一写入，
  循环不代写

状态词表：受理 ``accepted`` → 进行中 ``running``（含子态
``waiting_for_decision``，决策点等待）→ 终态 ``{succeeded / failed /
cancelled / timeout}``。写入规则：终态粘滞（终态后的任何写入被忽略）；
``accepted`` 不可回退；同状态重复写入幂等（快照刷新、不重发事件）。

红线：本模块只存发起方/执行者**名字字符串**（路由用）并经事件叫醒——
不 import Agent 层、不感知 LLM/Config；任务事实源是记录表查询，事件
只做通知（重启丢跟踪 = 有意取舍，见 ADR-013 已知边界）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Literal, Optional

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus
    from src.modules.tools.registry import ToolRegistry

logger = get_logger("TaskTracker")


# ---------------------------------------------------------------------------
# 状态词表与数据契约
# ---------------------------------------------------------------------------

# 任务状态：受理 → 进行中（含决策点等待子态）→ 终态
TaskStatus = Literal["accepted", "running", "waiting_for_decision", "succeeded", "failed", "cancelled", "timeout"]

TERMINAL_TASK_STATES = frozenset({"succeeded", "failed", "cancelled", "timeout"})
IN_PROGRESS_TASK_STATES = frozenset({"accepted", "running", "waiting_for_decision"})

# 事实源归属：provider 型 = 跟踪循环写；agent 型 = 执行 Agent 写
TaskSource = Literal["provider", "agent"]

# 摘要截断（事件 payload 防膨胀）
_MAX_SUMMARY_CHARS = 200


@dataclass(slots=True)
class TaskRecord:
    """任务记录表条目（内存态；终态移除）。"""

    task_id: str
    provider: str  # 提供者名（工具所属）
    tool: str  # 受理工具全名（定位归属 provider 用）
    initiator: str  # 发起方 Agent 注册名（变化通知谁）
    executor: str  # 执行者（provider 型 = 提供者名；agent 型 = 执行 Agent 名）
    status: TaskStatus
    source: TaskSource
    snapshot: Dict[str, Any] = field(default_factory=dict)  # 执行侧自由 dict（嵌套任务在此带内层任务号）
    updated_at_ms: int = 0
    created_at_ms: int = 0


# ---------------------------------------------------------------------------
# 任务记录表（单写者按 source 分工；状态单调 + 幂等 + 终态移除）
# ---------------------------------------------------------------------------


class TaskLedger:
    """任务记录表（内存 dict；对账副本——事实源在执行侧）。

    写入规则（``update``）：
    - 终态粘滞：已终态的任务忽略后续写入（记录已移除，按未知任务处理）
    - 同状态重复写入幂等：刷新快照与时间戳，**不**发事件
    - ``accepted``/``running`` 组内迁移静默记录（受理后转入进行中不值得
      唤醒发起方）；决策点与终态才是唤醒级变化
    - 真变化（唤醒级迁移）：更新记录 + 发 ``task.changed``；终态写入在
      发事件后移除条目
    """

    def __init__(self, event_bus: Optional["EventBus"] = None) -> None:
        self._event_bus = event_bus
        self._records: Dict[str, TaskRecord] = {}

    def register(
        self,
        *,
        task_id: str,
        provider: str,
        tool: str,
        initiator: str,
        executor: str,
        status: TaskStatus = "accepted",
        source: TaskSource,
        snapshot: Optional[Dict[str, Any]] = None,
    ) -> TaskRecord:
        """登记新任务（重复 task_id 幂等：保留先登记，返回既有记录）。"""
        existing = self._records.get(task_id)
        if existing is not None:
            logger.debug(f"任务 {task_id} 已登记（保留先登记，source={existing.source}）")
            return existing
        ts = now_ms()
        record = TaskRecord(
            task_id=task_id,
            provider=provider,
            tool=tool,
            initiator=initiator,
            executor=executor,
            status=status,
            source=source,
            snapshot=dict(snapshot or {}),
            updated_at_ms=ts,
            created_at_ms=ts,
        )
        self._records[task_id] = record
        logger.info(
            f"任务已登记: task_id={task_id} tool={tool} 发起方={initiator} "
            f"执行者={executor} source={source} status={status}"
        )
        return record

    def get(self, task_id: str) -> Optional[TaskRecord]:
        """按任务号查记录（终态已移除 → None）。"""
        return self._records.get(task_id)

    def active_task_ids(self) -> List[str]:
        """进行中任务号列表（快照排序，供跟踪循环遍历）。"""
        return sorted(self._records.keys())

    def __contains__(self, task_id: object) -> bool:
        return task_id in self._records

    def __len__(self) -> int:
        return len(self._records)

    def update(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        snapshot: Optional[Dict[str, Any]] = None,
        summary: str = "",
    ) -> Optional[TaskChangedPayload]:
        """写入状态变化（单写者按 source 分工：provider 型=跟踪循环、agent 型=执行 Agent）。

        Returns:
            真变化时返回已调度广播的 ``TaskChangedPayload``；幂等/回退/未知
            任务返回 ``None``（未发事件）。
        """
        record = self._records.get(task_id)
        if record is None:
            logger.debug(f"任务 {task_id} 不在记录表（未知或已终态移除），忽略写入 status={status}")
            return None

        if record.status == status:
            # 同状态幂等：不重发事件；快照**内容**变化才算新进展（刷新停滞
            # 计时基准），快照原样重复不刷新——无信息的轮询不应顺延停滞告警
            if snapshot is not None and dict(snapshot) != record.snapshot:
                record.snapshot = dict(snapshot)
                record.updated_at_ms = now_ms()
            return None

        # "继续后台跑"组内迁移（accepted <-> running）静默记录：受理后任务
        # 转入进行中不值得唤醒发起方——决策点（waiting_for_decision）与终态
        # 才是唤醒级变化（对齐 maicraft 既有行为：pending/running 不注入）
        _quiet_group = {"accepted", "running"}
        if record.status in _quiet_group and status in _quiet_group:
            record.status = status
            if snapshot is not None:
                record.snapshot = dict(snapshot)
            record.updated_at_ms = now_ms()
            logger.debug(f"任务 {task_id} 组内迁移 {record.status} -> {status}（静默，不发事件）")
            return None

        if status == "accepted" and record.status in IN_PROGRESS_TASK_STATES:
            logger.debug(f"任务 {task_id} 状态回退被忽略: {record.status} -> accepted")
            return None

        old_status = record.status
        record.status = status
        if snapshot is not None:
            record.snapshot = dict(snapshot)
        record.updated_at_ms = now_ms()

        text = summary or f"状态变化 {old_status} -> {status}"
        payload = TaskChangedPayload(
            task_id=task_id,
            status=status,
            summary=text[:_MAX_SUMMARY_CHARS],
            initiator=record.initiator,
            executor=record.executor,
            snapshot=dict(record.snapshot),
            timestamp_ms=record.updated_at_ms,
        )

        if status in TERMINAL_TASK_STATES:
            # 终态：先移除条目再广播（订阅方拿到的即最终快照）
            del self._records[task_id]
            logger.info(f"任务终态: task_id={task_id} status={status}（条目已移除，执行者={record.executor}）")
        else:
            logger.info(f"任务状态变化: task_id={task_id} {old_status} -> {status}")

        self._emit_changed(payload)
        return payload

    def alert_stall(self, task_id: str, *, summary: str) -> None:
        """发停滞告警（状态不变；``payload.alert=True`` 标记唤醒类通知）。

        无进展提醒语义：不杀任务、不改进度——把"该看看了"送给发起方；
        刷新 ``updated_at_ms``（告警周期顺延由调用方的间隔记账控制）。
        """
        record = self._records.get(task_id)
        if record is None:
            return
        record.updated_at_ms = now_ms()
        payload = TaskChangedPayload(
            task_id=task_id,
            status=record.status,
            summary=summary[:_MAX_SUMMARY_CHARS],
            initiator=record.initiator,
            executor=record.executor,
            snapshot=dict(record.snapshot),
            timestamp_ms=record.updated_at_ms,
            alert=True,
        )
        logger.warning(f"任务停滞告警: task_id={task_id} status={record.status}")
        self._emit_changed(payload)

    def _emit_changed(self, payload: TaskChangedPayload) -> None:
        """广播 task.changed（后台任务调度，不阻塞写入方）；失败仅记日志。

        无事件总线或无运行循环（同步测试上下文）时跳过调度——记录表仍是
        事实源，事件只是通知。
        """
        if self._event_bus is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("无运行循环，task.changed 跳过调度（同步测试上下文）")
            return
        loop.create_task(self._emit_async(payload))

    async def _emit_async(self, payload: TaskChangedPayload) -> None:
        """异步广播（异常为观测旁路，不上抛）。"""
        try:
            await self._event_bus.emit(CoreEvents.TASK_CHANGED, payload, source="TaskLedger")  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 - 观测旁路
            logger.warning(f"task.changed 广播失败（task_id={payload.task_id}）: {exc}")


# ---------------------------------------------------------------------------
# 跟踪循环（provider 型任务的周期兜底 + 通知触发；订阅起停归它）
# ---------------------------------------------------------------------------

# 通知适配器回调形态：收到执行侧提示（举旗级，可丢）→ 触发一次核实；
# 提供者级订阅（如 MCP attention 资源）不知道具体任务号，传空串即可
TaskNotificationCallback = Callable[[str], None]
# 退订句柄（订阅起停归跟踪循环；该提供者名下无进行中任务时退订）
Unsubscribe = Callable[[], None]


class TaskTracker:
    """provider 型任务的跟踪服务（照 ``ToolHealthMonitor``：循环 + 可测单步）。

    - ``track(...)``：登记进记录表并订阅执行侧通知（通知适配器按**提供者
      复用**——首个任务触发订阅，名下任务清空后退订）
    - ``step()``：单步核实——对每个进行中的 provider 型任务，经归属
      provider 的查询适配器拉取快照并写入记录表（真变化才发事件）；
      附带无进展提醒（``wait_timeout_ms`` 停滞 → 告警不杀任务，顺延一个
      周期）。**测试直接驱动本方法，无需 sleep**
    - ``notify(task_id)``：通知适配器回调（举旗级、非阻塞——可能跑在
      provider 消息循环内）；唤醒循环提前进入一次 ``step``
    - agent 型任务（委派）：执行 Agent 唯一写入，本循环不代写
    """

    def __init__(
        self,
        registry: "ToolRegistry",
        ledger: TaskLedger,
        *,
        poll_interval_ms: int = 2000,
        wait_timeout_ms: int = 1_800_000,
    ) -> None:
        self._registry = registry
        self._ledger = ledger
        self._poll_interval_ms = poll_interval_ms
        self._wait_timeout_ms = wait_timeout_ms
        self._wakeup = asyncio.Event()
        # 订阅按提供者复用（非每任务）：provider 名 → 退订句柄
        self._subscriptions: Dict[str, Unsubscribe] = {}
        # 停滞告警间隔记账（告警后顺延一个 wait_timeout 周期）
        self._stall_alerted_at: Dict[str, int] = {}
        self._task: Optional[asyncio.Task[None]] = None
        self._stopping = False

    @property
    def poll_interval_ms(self) -> int:
        return self._poll_interval_ms

    @property
    def ledger(self) -> TaskLedger:
        """记录表引用（Agent 侧查询自己名下任务用；写入仍走单写者规则）。"""
        return self._ledger

    def start(self) -> None:
        """启动跟踪循环（非阻塞；已启动则跳过）。无进行中任务时靠周期兜底节拍。"""
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="task-tracker")

    async def stop(self) -> None:
        """停止跟踪循环并退订全部通知（等待上限 5 秒）。"""
        self._stopping = True
        self._wakeup.set()
        task = self._task
        if task is not None:
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("TaskTracker 循环取消等待超时（5s），放弃等待继续停机")
            self._task = None
        self._unsubscribe_all()

    def track(
        self,
        *,
        task_id: str,
        provider: str,
        tool: str,
        initiator: str,
        executor: Optional[str] = None,
        snapshot: Optional[Dict[str, Any]] = None,
    ) -> None:
        """登记 provider 型任务并订阅执行侧通知（受理回执路径调用）。

        执行者缺省 = 提供者名（provider 型任务的执行侧就是该提供者）。
        """
        self._ledger.register(
            task_id=task_id,
            provider=provider,
            tool=tool,
            initiator=initiator,
            executor=executor or provider,
            status="accepted",
            source="provider",
            snapshot=snapshot,
        )
        self._ensure_subscribed(provider, tool)
        self._wakeup.set()  # 登记即触发一次首查

    def notify(self, task_id: str = "") -> None:
        """通知适配器回调（举旗级、可丢）：触发一次核实。

        提供者级订阅（如 MCP attention 资源）不知道具体任务号——传空串
        即可（通知只是提示，核实由 step 对全部进行中任务做）。
        """
        self._wakeup.set()

    async def _loop(self) -> None:
        """跟踪主循环：醒 → 单步核实 → 等下一旗（通知举旗或周期兜底节拍）。"""
        try:
            while not self._stopping:
                try:
                    await self.step()
                except Exception as exc:  # noqa: BLE001 - 单步失败不杀循环
                    logger.error(f"TaskTracker 单步核实异常: {exc}", exc_info=True)
                # 先做后等：睡中到来的通知不会丢失（Event 在 wait 前已置位则立即醒）
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=self._poll_interval_ms / 1000)
                except asyncio.TimeoutError:
                    pass
                self._wakeup.clear()
        except asyncio.CancelledError:
            pass

    async def step(self) -> None:
        """单步核实全部进行中任务（provider 型经查询适配器；agent 型跳过）。

        附带：无进展提醒（``wait_timeout_ms`` 停滞 → 告警不杀任务，顺延
        一个周期）与孤立订阅退订（该提供者名下已无进行中任务）。
        拆出供测试直接驱动；无任务时为空操作。
        """
        active_providers: Dict[str, int] = {}
        for task_id in self._ledger.active_task_ids():
            record = self._ledger.get(task_id)
            if record is None:
                continue
            if record.source == "provider":
                await self._verify_one(record)
                if self._ledger.get(task_id) is not None:  # 终态核实后条目已移除
                    active_providers[record.provider] = active_providers.get(record.provider, 0) + 1
                else:
                    self._stall_alerted_at.pop(task_id, None)  # 任务收尾清告警记账
                    continue
            self._check_stall(record)
        # 订阅按提供者复用：名下无进行中任务的提供者退订
        for provider in list(self._subscriptions.keys()):
            if provider not in active_providers:
                self._release_subscription(provider)

    def _check_stall(self, record: TaskRecord) -> None:
        """无进展提醒：``updated_at_ms`` 距今超过 ``wait_timeout_ms`` → 停滞告警。

        告警经 ``task.changed`` 承载（``payload.alert=True``，status 保持
        当前值——告警是唤醒类通知不是状态变化）；不杀任务。同任务两个
        wait_timeout 周期内只告警一次（告警后顺延）。
        """
        if self._wait_timeout_ms <= 0:
            return
        if now_ms() - record.updated_at_ms < self._wait_timeout_ms:
            return
        if now_ms() - self._stall_alerted_at.get(record.task_id, 0) < self._wait_timeout_ms:
            return  # 本周期已告警过（顺延语义）
        self._stall_alerted_at[record.task_id] = now_ms()
        self._ledger.alert_stall(
            record.task_id,
            summary=(
                f"后台任务 {record.task_id} 已 {self._wait_timeout_ms // 1000}s 无进展（告警不终止任务，请检查或等待）"
            ),
        )

    async def _verify_one(self, record: TaskRecord) -> None:
        """核实单个 provider 型任务：查询适配器 → 记录表写入（真变化才发事件）。"""
        owner = self._registry.provider_of_tool(record.tool)
        if owner is None:
            logger.debug(f"任务 {record.task_id} 的工具 {record.tool} 无归属 provider，跳过核实")
            return
        query = getattr(owner, "query_task", None)
        if not callable(query):
            logger.debug(f"provider '{record.provider}' 不支持查询适配器（query_task），跳过核实")
            return
        try:
            result = await query(record.task_id)
        except Exception as exc:  # noqa: BLE001 - 查询失败按无新事实处理
            logger.warning(
                f"任务 {record.task_id} 查询适配器异常（provider={record.provider}）: {type(exc).__name__}: {exc}"
            )
            return
        if not isinstance(result, dict):
            return
        new_status = result.get("status")
        if new_status not in IN_PROGRESS_TASK_STATES and new_status not in TERMINAL_TASK_STATES:
            logger.debug(f"任务 {record.task_id} 查询返回未知状态 {new_status!r}，忽略")
            return
        snapshot = result.get("snapshot")
        self._ledger.update(
            record.task_id,
            new_status,
            snapshot=snapshot if isinstance(snapshot, dict) else None,
            summary=str(result.get("summary") or ""),
        )
        # 终态条目已移除；订阅退订统一由 step 尾部按提供者余量判定

    # ----- 订阅起停（归跟踪循环；按提供者复用） -----

    def _ensure_subscribed(self, provider: str, tool: str) -> None:
        """订阅执行侧通知（首个任务触发，重复调用幂等）。"""
        if provider in self._subscriptions:
            return
        owner = self._registry.provider_of_tool(tool)
        if owner is None:
            return
        subscribe = getattr(owner, "subscribe_task_notifications", None)
        if not callable(subscribe):
            return
        try:
            unsubscribe = subscribe(self.notify)
        except Exception as exc:  # noqa: BLE001 - 订阅失败降级周期兜底
            logger.warning(f"provider '{provider}' 通知订阅失败（降级周期兜底）: {type(exc).__name__}: {exc}")
            return
        if callable(unsubscribe):
            self._subscriptions[provider] = unsubscribe
            logger.debug(f"provider '{provider}' 已订阅执行侧通知（随任务起停复用）")

    def _release_subscription(self, provider: str) -> None:
        """退订指定提供者的执行侧通知（名下已无进行中任务）。"""
        unsubscribe = self._subscriptions.pop(provider, None)
        if unsubscribe is None:
            return
        try:
            unsubscribe()
            logger.debug(f"provider '{provider}' 已退订执行侧通知")
        except Exception as exc:  # noqa: BLE001 - 退订失败不阻断
            logger.warning(f"provider '{provider}' 退订异常（忽略）: {exc}")

    def _unsubscribe_all(self) -> None:
        for provider in list(self._subscriptions.keys()):
            self._release_subscription(provider)


# ---------------------------------------------------------------------------
# [tools.tasks] 配置兜底读取（正式配置段由配置线落，本函数只读不写）
# ---------------------------------------------------------------------------


def resolve_tasks_config(
    tools_cfg: Optional[Dict[str, Any]],
    agents_cfg: Optional[Dict[str, Any]],
) -> tuple[int, int]:
    """解析任务基建节拍配置：``(poll_interval_ms, wait_timeout_ms)``。

    读取顺序（新键优先，旧键过渡兼容，最后默认）：
    1. ``[tools.tasks].poll_interval_ms / wait_timeout_ms``（正式段，配置线落）
    2. ``[agents.minecraft].execute_poll_interval_ms / execute_wait_timeout_ms``
       （旧键上收来源；minecraft 侧沿用多年）
    3. 默认 ``(2000, 1800000)``
    """
    tools = tools_cfg if isinstance(tools_cfg, dict) else {}
    agents = agents_cfg if isinstance(agents_cfg, dict) else {}

    tasks = tools.get("tasks")
    tasks = tasks if isinstance(tasks, dict) else {}
    minecraft = agents.get("minecraft")
    minecraft = minecraft if isinstance(minecraft, dict) else {}

    def _pick(new_val: Any, old_val: Any, default: int) -> int:
        for v in (new_val, old_val):
            if isinstance(v, int) and v > 0:
                return v
        return default

    poll = _pick(tasks.get("poll_interval_ms"), minecraft.get("execute_poll_interval_ms"), 2000)
    wait_timeout = _pick(tasks.get("wait_timeout_ms"), minecraft.get("execute_wait_timeout_ms"), 1_800_000)
    return poll, wait_timeout


__all__ = [
    "TaskStatus",
    "TERMINAL_TASK_STATES",
    "IN_PROGRESS_TASK_STATES",
    "TaskSource",
    "TaskRecord",
    "TaskLedger",
    "TaskTracker",
    "resolve_tasks_config",
]
