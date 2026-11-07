"""BackgroundTaskRegistry：登记 / 更新进度 / 注销三个动作，每次变更广播 background.task.changed。"""

from __future__ import annotations

from typing import List

from src.modules.background import BackgroundTaskRegistry
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.background import BackgroundTaskChangedPayload


def _registry_with_recorder() -> tuple[BackgroundTaskRegistry, EventBus, List[BackgroundTaskChangedPayload]]:
    """装配登记处 + 真实 EventBus，收集全部变更载荷供断言。"""
    bus = EventBus()
    payloads: List[BackgroundTaskChangedPayload] = []

    async def record(event_name: str, payload: BackgroundTaskChangedPayload, source: str) -> None:
        payloads.append(payload)

    bus.on(CoreEvents.BACKGROUND_TASK_CHANGED, record, model_class=BackgroundTaskChangedPayload)
    return BackgroundTaskRegistry(bus), bus, payloads


async def test_begin_update_finish_broadcast_full_state() -> None:
    """登记 → 报进度 → 完成注销：每次变更发一条事件，载荷带该任务完整现状。"""
    registry, bus, payloads = _registry_with_recorder()

    await registry.begin("settle-58", "整理场次：2026-11-06 周五 22:22")
    await registry.update("settle-58", "第 2 笔/共 3 笔")
    assert [(row.task_id, row.state, row.progress_text) for row in registry.snapshot()] == [
        ("settle-58", "running", "第 2 笔/共 3 笔")
    ]

    await registry.finish("settle-58", success=True)
    await bus.wait_until_drained()

    assert [payload.state for payload in payloads] == ["running", "running", "finished"]
    assert payloads[0].task_id == "settle-58"
    assert payloads[0].title == "整理场次：2026-11-06 周五 22:22"
    assert payloads[0].started_at_ms > 0
    # 终态载荷带注销前的最后进度，前端弹通知与摘行都用它
    assert payloads[-1].progress_text == "第 2 笔/共 3 笔"
    assert payloads[-1].failure_reason is None
    # 终态任务从注册表移除（查询口不再返回，前端靠事件摘行）
    assert registry.snapshot() == []


async def test_finish_failure_carries_reason() -> None:
    """失败注销：state=failed 且带原因；成功路径不带原因。"""
    registry, bus, payloads = _registry_with_recorder()

    await registry.begin("memory-index-backfill", "记忆索引补算")
    await registry.finish("memory-index-backfill", success=False, failure_reason="这一轮补算失败：连接超时")
    await bus.wait_until_drained()

    assert payloads[-1].state == "failed"
    assert payloads[-1].failure_reason == "这一轮补算失败：连接超时"
    assert registry.snapshot() == []


async def test_unknown_task_id_is_ignored_without_raising() -> None:
    """未登记的任务号报进度 / 注销：记日志忽略，不让调用方炸（登记处不挡任务方）。"""
    registry, bus, payloads = _registry_with_recorder()

    await registry.update("ghost", "进度文本")
    await registry.finish("ghost", success=True)
    await bus.wait_until_drained()

    assert payloads == [] and registry.snapshot() == []


async def test_duplicate_begin_overwrites_with_warning() -> None:
    """同一任务号重复登记：按重新开始覆盖旧行（旧行忘了注销的调用方不把登记处搞挂）。"""
    registry, bus, payloads = _registry_with_recorder()

    await registry.begin("settle-9", "旧标题")
    await registry.update("settle-9", "旧进度")
    await registry.begin("settle-9", "整理场次：2026-11-07 周六 10:00")
    await registry.finish("settle-9", success=True)
    await bus.wait_until_drained()

    assert [payload.title for payload in payloads][-1] == "整理场次：2026-11-07 周六 10:00"
    assert registry.snapshot() == []
