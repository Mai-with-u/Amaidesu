"""异步任务基建：记录表写入规则（幂等/静默迁移/回退忽略/终态粘滞）、跟踪循环单步、订阅起停、节拍配置、事件载荷"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TERMINAL_TASK_STATES, TaskLedger, TaskTracker, resolve_tasks_config


class FakeReceiptProvider(BaseToolProvider):
    """回执型工具替身：query_task 按 states 队列应答；订阅/退订可断言。"""

    def __init__(self, states: List[Dict[str, Any]] | None = None) -> None:
        self.name = "game"
        self._states = list(states or [])
        self.subscribed: List[str] = []
        self.unsubscribed: List[str] = []

    def list_tools(self):
        return [ToolSpec(name="execute", description="受理型执行", kind="sync", provider="game")]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=invocation.tool_name, success=True, structured_content={"accepted": True, "task_id": "task-1"}
        )

    async def query_task(self, task_id: str):
        if not self._states:
            return None
        item = self._states.pop(0)
        return {"status": item.get("status"), "snapshot": item.get("snapshot", {}), "summary": item.get("summary", "")}

    def subscribe_task_notifications(self, callback) -> Any:
        self.subscribed.append("sub")
        return lambda: self.unsubscribed.append("unsub")


def _setup(states: List[Dict[str, Any]] | None = None):
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    provider = FakeReceiptProvider(states)
    registry.register_provider(provider)
    ledger = TaskLedger(event_bus=bus)
    return ledger, TaskTracker(registry, ledger, poll_interval_ms=10), bus, provider


def _registered_ledger(task_id: str, **kwargs) -> TaskLedger:
    ledger = TaskLedger()
    ledger.register(
        task_id=task_id,
        provider="game",
        tool="game_execute",
        initiator="minecraft",
        executor="game",
        source="provider",
        **kwargs,
    )
    return ledger


async def _drain() -> None:
    """等 fire-and-forget 的 task.changed 派发链跑完。

    固定时长 sleep 会与派发链竞态；以「挂起任务集合清空」为确定性完成信号，
    但全量套件下前序测试可能在共享循环上留下永不完成的任务，故配 2 秒总时限兜底。
    """
    deadline = asyncio.get_running_loop().time() + 2.0
    while True:
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        remaining = deadline - asyncio.get_running_loop().time()
        if not pending or remaining <= 0:
            return
        await asyncio.wait(pending, timeout=min(remaining, 0.5))


async def test_state_transitions_drive_events_idempotently_and_remove_on_terminal() -> None:
    ledger, tracker, bus, _ = _setup(
        states=[
            {"status": "running"},  # accepted→running 组内迁移：静默
            {"status": "running"},  # 同状态：幂等
            {"status": "waiting_for_decision"},  # 决策点：唤醒级 → 事件 1
            {"status": "succeeded", "snapshot": {"done": True}},  # 终态 → 事件 2 + 移除
        ]
    )
    received: List[TaskChangedPayload] = []

    async def on_change(name: str, payload: TaskChangedPayload, source: str) -> None:
        received.append(payload)

    bus.on(CoreEvents.TASK_CHANGED, on_change, TaskChangedPayload)
    tracker.track(task_id="t1", provider="game", tool="game_execute", initiator="minecraft")
    for _ in range(4):
        await tracker.step()
    await _drain()

    assert [p.status for p in received] == ["waiting_for_decision", "succeeded"]  # 组内迁移静默 + 幂等不重发
    assert "t1" not in ledger
    final = received[-1]
    assert final.snapshot == {"done": True} and (final.initiator, final.executor) == ("minecraft", "game")
    assert final.source == "provider"  # 事件带事实源归属，观察面据此推导任务归属


def test_ledger_write_rules_terminal_sticky_quiet_group_and_no_regression() -> None:
    ledger = _registered_ledger("t")
    assert ledger.update("t", "running", snapshot={"phase": 1}) is None  # accepted<->running 组内迁移静默
    record = ledger.get("t")
    assert (record.status, record.snapshot) == ("running", {"phase": 1})  # 但状态与快照照常记录

    assert ledger.update("t", "waiting_for_decision") is not None  # 进入决策点发事件
    assert ledger.update("t", "accepted") is None  # 决策点不可回退为受理
    assert ledger.get("t").status == "waiting_for_decision"

    first = ledger.update("t", "succeeded")
    assert first is not None and first.status == "succeeded" and "t" not in ledger
    assert ledger.update("t", "running") is None  # 终态粘滞：移除后写入按未知任务忽略


def test_same_status_snapshot_change_refreshes_stall_clock_only_when_content_changes() -> None:
    ledger = _registered_ledger("t", status="running", snapshot={"progress": 1})
    before = ledger.get("t").updated_at_ms
    assert ledger.update("t", "running", snapshot={"progress": 1}) is None
    assert ledger.get("t").updated_at_ms == before  # 原样重复不刷新停滞计时
    assert ledger.update("t", "running", snapshot={"progress": 2}) is None  # 内容变化刷新计时但不发事件
    record = ledger.get("t")
    assert record.snapshot == {"progress": 2} and record.updated_at_ms >= before


async def test_subscription_follows_task_lifetime_and_notify_wakes_step_without_poll() -> None:
    _, tracker, _, provider = _setup(states=[{"status": "succeeded"}])
    assert provider.subscribed == []  # 无任务不订阅
    tracker.track(task_id="t4", provider="game", tool="game_execute", initiator="minecraft")
    assert len(provider.subscribed) == 1
    await tracker.step()  # 终态 → 移除 + 退订
    assert len(provider.unsubscribed) == 1

    ledger, woken, _, _ = _setup(states=[{"status": "running"}])
    woken.track(task_id="t5", provider="game", tool="game_execute", initiator="minecraft")
    woken._wakeup.clear()  # 模拟登记与通知分离
    woken.start()
    try:
        woken.notify("t5")  # 通知举旗，循环提前核实，不依赖周期节拍
        await _drain()
        assert ledger.get("t5").status == "running"
    finally:
        await woken.stop()


def test_resolve_tasks_config_fills_defaults_per_key() -> None:
    assert resolve_tasks_config({"tasks": {"poll_interval_ms": 500, "wait_timeout_ms": 60000}}) == (500, 60000)
    assert resolve_tasks_config({"tasks": {"poll_interval_ms": 1500}}) == (1500, 1_800_000)  # 只给一项，另一项回落默认
    assert resolve_tasks_config(None) == (2000, 1_800_000)


def test_task_changed_event_constant_payload_shape_and_terminal_vocabulary() -> None:
    assert CoreEvents.TASK_CHANGED == "task.changed" and "task.changed" in CoreEvents.get_all_events()
    data = TaskChangedPayload(
        task_id="t-1", status="running", initiator="streamer", executor="minecraft", snapshot={"inner_task_id": "m-7"}
    ).model_dump()
    assert (data["task_id"], data["status"], data["initiator"], data["executor"]) == (
        "t-1",
        "running",
        "streamer",
        "minecraft",
    )
    assert data["source"] == "" and data["snapshot"] == {"inner_task_id": "m-7"}  # 嵌套委派快照带内层任务号
    assert TERMINAL_TASK_STATES == frozenset({"succeeded", "failed", "cancelled", "timeout"})
