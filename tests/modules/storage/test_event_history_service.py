"""EventHistoryService 纯内存环形缓冲单测。

事件历史定位为"运行周期观察窗"：只测内存行为（record / get_recent /
maxlen 淘汰）；持久观察由业务表各自的事实源承担，表持久化路径已移除。
"""

from __future__ import annotations

import pytest

from src.modules.events.event_history import EventHistoryService, EventRecord


@pytest.mark.asyncio
async def test_record_keeps_buffer_order() -> None:
    """record 顺序进缓冲，get_recent 倒序（最新在前）。"""
    service = EventHistoryService(max_events=10)
    for idx in range(3):
        service.record(EventRecord(id=f"evt-{idx}", type="room.message", source="test", summary="s"))
    recent = service.get_recent(10)
    assert [r.id for r in recent] == ["evt-2", "evt-1", "evt-0"]


@pytest.mark.asyncio
async def test_maxlen_evicts_oldest() -> None:
    """超过容量后自动淘汰最旧记录。"""
    service = EventHistoryService(max_events=5)
    for idx in range(8):
        service.record(EventRecord(id=f"evt-{idx}", type="room.message", source="test", summary="s"))
    recent = service.get_recent(100)
    assert len(recent) == 5
    assert [r.id for r in reversed(recent)] == ["evt-3", "evt-4", "evt-5", "evt-6", "evt-7"]


@pytest.mark.asyncio
async def test_zero_max_events_rejected() -> None:
    """非正容量在构造期报错。"""
    with pytest.raises(ValueError, match="max_events"):
        EventHistoryService(max_events=0)


@pytest.mark.asyncio
async def test_record_without_event_loop_keeps_buffer() -> None:
    """纯同步上下文下 record 仍可用（仅内存缓冲）。"""
    service = EventHistoryService(max_events=10)
    service.record(EventRecord(id="evt-1", type="room.message", source="test", summary="s"))
    assert service.get_recent(1)[0].id == "evt-1"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
