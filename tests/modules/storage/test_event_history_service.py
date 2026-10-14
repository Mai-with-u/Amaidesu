"""EventHistoryService：运行周期内的纯内存环形观察窗"""

from __future__ import annotations

import pytest

from src.modules.events.event_history import EventHistoryService, EventRecord


def _record(service: EventHistoryService, count: int) -> None:
    for idx in range(count):
        service.record(EventRecord(id=f"evt-{idx}", type="room.message", source="test", summary="s"))


def test_recent_is_newest_first_and_capacity_evicts_oldest() -> None:
    service = EventHistoryService(max_events=10)
    _record(service, 3)
    assert [r.id for r in service.get_recent(10)] == ["evt-2", "evt-1", "evt-0"]

    service = EventHistoryService(max_events=5)
    _record(service, 8)
    assert [r.id for r in service.get_recent(100)] == ["evt-7", "evt-6", "evt-5", "evt-4", "evt-3"]


def test_non_positive_capacity_rejected() -> None:
    with pytest.raises(ValueError, match="max_events"):
        EventHistoryService(max_events=0)
