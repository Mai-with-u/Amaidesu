"""异步任务基建单测（ADR-013 六件套）。

测试切入点 = 跟踪循环单步函数（``TaskTracker.step``）与记录表写入规则；
无 sleep 依赖（事件驱动断言用 EventBus 直收）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import (
    TERMINAL_TASK_STATES,
    TaskLedger,
    TaskTracker,
    resolve_tasks_config,
)


# ---------------------------------------------------------------------------
# 测试用 provider 型工具（查询/通知适配器可控）
# ---------------------------------------------------------------------------


class FakeReceiptProvider(BaseToolProvider):
    """回执型工具替身：query_task 按 states 队列应答；订阅可断言。"""

    def __init__(self, states: List[Dict[str, Any]] | None = None) -> None:
        self.name = "game"
        self._states = list(states or [])
        self.subscribed: List[str] = []
        self.unsubscribed: List[str] = []

    def list_tools(self):
        return [ToolSpec(name="execute", description="受理型执行", kind="sync", provider="game")]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=True,
            structured_content={"accepted": True, "task_id": "task-1"},
        )

    async def query_task(self, task_id: str):
        if not self._states:
            return None
        item = self._states.pop(0)
        return {"status": item.get("status"), "snapshot": item.get("snapshot", {}), "summary": item.get("summary", "")}

    def subscribe_task_notifications(self, callback) -> Any:
        self.subscribed.append("sub")

        def _unsubscribe() -> None:
            self.unsubscribed.append("unsub")

        return _unsubscribe


def _setup(states: List[Dict[str, Any]] | None = None) -> tuple[ToolRegistry, TaskLedger, TaskTracker, EventBus, FakeReceiptProvider]:
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    provider = FakeReceiptProvider(states)
    registry.register_provider(provider)
    ledger = TaskLedger(event_bus=bus)
    tracker = TaskTracker(registry, ledger, poll_interval_ms=10)
    return registry, ledger, tracker, bus, provider


async def _drain() -> None:
    """让 fire-and-forget 的 task.changed 派发任务跑完。"""
    await asyncio.sleep(0.02)


# ---------------------------------------------------------------------------
# 记录表写入规则：状态变化 / 幂等 / 回退忽略 / 终态移除
# ---------------------------------------------------------------------------


async def test_state_transitions_drive_events_and_idempotent() -> None:
    """状态变化 → 更新 + 发事件；同状态重复 → 不发；终态 → 移除。"""
    registry, ledger, tracker, bus, provider = _setup(
        states=[
            {"status": "running"},  # 组内迁移（accepted→running）：静默
            {"status": "running"},  # 同状态：幂等
            {"status": "waiting_for_decision"},  # 决策点：唤醒级 → 事件 1
            {"status": "succeeded", "snapshot": {"done": True}},  # 终态 → 事件 2 + 移除
        ]
    )
    received: List[TaskChangedPayload] = []

    async def _on(name: str, payload: TaskChangedPayload, source: str) -> None:
        received.append(payload)

    bus.on(CoreEvents.TASK_CHANGED, _on, TaskChangedPayload)

    tracker.track(task_id="t1", provider="game", tool="game_execute", initiator="minecraft")
    await tracker.step()  # running（组内迁移 → 静默）
    await tracker.step()  # running（幂等 → 不发）
    await tracker.step()  # waiting_for_decision（决策点 → 事件）
    await tracker.step()  # succeeded（终态 → 事件 + 移除）
    await _drain()

    assert [p.status for p in received] == ["waiting_for_decision", "succeeded"], (
        "唤醒级变化才发事件（组内迁移静默 + 幂等不重发）"
    )
    assert "t1" not in ledger, "终态后条目移除"
    # 终态事件携带最终快照
    assert received[-1].snapshot == {"done": True}
    assert received[-1].initiator == "minecraft"
    assert received[-1].executor == "game"


async def test_terminal_state_is_sticky_via_ledger() -> None:
    """终态粘滞：条目移除后（等同终态后）任何写入被忽略，不再发事件。"""
    ledger = TaskLedger()
    ledger.register(
        task_id="t9", provider="game", tool="game_execute", initiator="minecraft", executor="game", source="provider"
    )
    first = ledger.update("t9", "succeeded")
    assert first is not None and first.status == "succeeded"
    assert "t9" not in ledger
    again = ledger.update("t9", "running")
    assert again is None, "终态粘滞：移除后的写入按未知任务忽略"


def test_quiet_group_transition_recorded_silently() -> None:
    """accepted <-> running 组内迁移：状态记录、不发事件（不唤醒发起方）。"""
    ledger = TaskLedger()
    ledger.register(
        task_id="t2", provider="game", tool="game_execute", initiator="minecraft", executor="game", source="provider"
    )
    assert ledger.update("t2", "running", snapshot={"phase": 1}) is None, "组内迁移静默"
    record = ledger.get("t2")
    assert record is not None and record.status == "running"
    assert record.snapshot == {"phase": 1}, "快照照常刷新"


def test_waiting_for_decision_regression_to_accepted_ignored() -> None:
    """决策点不可回退为受理（决策点与受理不同组）。"""
    ledger = TaskLedger()
    ledger.register(
        task_id="t7", provider="game", tool="game_execute", initiator="minecraft", executor="game", source="provider"
    )
    first = ledger.update("t7", "waiting_for_decision")
    assert first is not None, "进入决策点发事件"
    assert ledger.update("t7", "accepted") is None, "决策点 → accepted 回退被忽略"
    record = ledger.get("t7")
    assert record is not None and record.status == "waiting_for_decision"


def test_same_status_snapshot_change_refreshes_stall_clock() -> None:
    """同状态幂等不发事件；快照内容变化刷新停滞计时，原样重复不刷新。"""
    ledger = TaskLedger()
    ledger.register(
        task_id="t3",
        provider="game",
        tool="game_execute",
        initiator="minecraft",
        executor="game",
        source="provider",
        status="running",
        snapshot={"progress": 1},
    )
    before = ledger.get("t3").updated_at_ms
    assert ledger.update("t3", "running", snapshot={"progress": 1}) is None  # 原样重复：不刷新计时
    assert ledger.get("t3").updated_at_ms == before
    assert ledger.update("t3", "running", snapshot={"progress": 2}) is None  # 内容变化：刷新计时（仍不发事件）
    record = ledger.get("t3")
    assert record.snapshot == {"progress": 2} and record.updated_at_ms >= before


# ---------------------------------------------------------------------------
# 订阅起停随任务（通知适配器）
# ---------------------------------------------------------------------------


async def test_subscription_starts_and_stops_with_task() -> None:
    """登记任务 → 订阅被调一次；任务清空（终态）→ 退订被调。"""
    registry, ledger, tracker, bus, provider = _setup(states=[{"status": "succeeded"}])

    assert provider.subscribed == [], "无任务：不订阅"
    tracker.track(task_id="t4", provider="game", tool="game_execute", initiator="minecraft")
    assert len(provider.subscribed) == 1, "登记任务：订阅一次"

    await tracker.step()  # 终态 → 移除 + 退订
    assert len(provider.unsubscribed) == 1, "任务清空：退订"


async def test_wakeup_flag_triggers_step_without_poll() -> None:
    """通知举旗 → 循环提前核实（不依赖周期节拍）。"""
    registry, ledger, tracker, bus, provider = _setup(states=[{"status": "running"}])
    tracker.track(task_id="t5", provider="game", tool="game_execute", initiator="minecraft")
    tracker._wakeup.clear()  # 登记后清旗，模拟"登记与通知分离"
    tracker.start()
    try:
        tracker.notify("t5")  # 通知举旗
        await _drain()
        record = ledger.get("t5")
        assert record is not None and record.status == "running", "举旗驱动核实（非轮询依赖）"
    finally:
        await tracker.stop()


# ---------------------------------------------------------------------------
# [tools.tasks] 三档兜底读取
# ---------------------------------------------------------------------------


def test_resolve_tasks_config_new_key_wins() -> None:
    poll, wait = resolve_tasks_config(
        {"tasks": {"poll_interval_ms": 500, "wait_timeout_ms": 60000}},
        {"minecraft": {"execute_poll_interval_ms": 2000, "execute_wait_timeout_ms": 1800000}},
    )
    assert (poll, wait) == (500, 60000)


def test_resolve_tasks_config_falls_back_to_legacy_key() -> None:
    poll, wait = resolve_tasks_config(
        {"memory": {"enabled": True}},
        {"minecraft": {"execute_poll_interval_ms": 1500, "execute_wait_timeout_ms": 900000}},
    )
    assert (poll, wait) == (1500, 900000)


def test_resolve_tasks_config_defaults_when_missing() -> None:
    poll, wait = resolve_tasks_config(None, None)
    assert (poll, wait) == (2000, 1_800_000)


# ---------------------------------------------------------------------------
# 事件常量与 payload 形状
# ---------------------------------------------------------------------------


def test_task_changed_constant_registered() -> None:
    assert CoreEvents.TASK_CHANGED == "task.changed"
    # 常量在反射收集范围（具体事件名）
    assert "task.changed" in CoreEvents.get_all_events()


def test_task_changed_payload_shape() -> None:
    payload = TaskChangedPayload(
        task_id="t-1",
        status="running",
        initiator="streamer",
        executor="minecraft",
        snapshot={"inner_task_id": "maicraft-7"},
    )
    data = payload.model_dump()
    assert data["task_id"] == "t-1"
    assert data["status"] == "running"
    assert data["initiator"] == "streamer"
    assert data["executor"] == "minecraft"
    assert data["snapshot"] == {"inner_task_id": "maicraft-7"}, "嵌套委派快照带内层任务号"


def test_terminal_states_vocabulary() -> None:
    assert TERMINAL_TASK_STATES == frozenset({"succeeded", "failed", "cancelled", "timeout"})
