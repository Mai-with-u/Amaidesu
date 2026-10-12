"""session_timeline 服务层单测：合并排序 / 条数截断 / kind 映射 / 入参不可变。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from src.modules.dashboard.services.session_timeline import build_timeline_items


@dataclass
class _FakeRecord:
    """EventRecord 的最小替身（时间线只读 type / timestamp_ms / data）。"""

    type: str
    timestamp_ms: int
    data: Dict[str, Any]


class _FakeHistory:
    """get_by_session 的最小替身。"""

    def __init__(self, records: List[_FakeRecord]) -> None:
        self._records = records

    def get_by_session(self, session_id: int, limit: int) -> List[_FakeRecord]:
        return self._records[:limit]


def test_kind_mapping_gift_and_super_chat() -> None:
    """gift_row/super_chat_row 映射为卡片 kind；其余 kind 直通。"""
    rows = [
        {"kind": "gift_row", "ts_ms": 2, "gift_name": "小花花"},
        {"kind": "super_chat_row", "ts_ms": 1, "amount": 30.0},
        {"kind": "danmaku", "ts_ms": 3, "content": "你好"},
    ]
    items = build_timeline_items(rows, None, session_id=1, limit=50)
    assert [i["kind"] for i in items] == ["super_chat", "gift", "danmaku"]
    # 事件历史缺省时不产生 event 条目
    assert all(i["kind"] != "event" for i in items)


def test_input_rows_not_mutated() -> None:
    """kind 映射构造新条目，不原地改写入参。"""
    rows: List[Dict[str, Any]] = [{"kind": "gift_row", "ts_ms": 1, "gift_name": "辣条"}]
    build_timeline_items(rows, None, session_id=1, limit=10)
    assert rows[0]["kind"] == "gift_row"


def test_merge_sort_and_event_filter() -> None:
    """明细行与事件历史按 ts_ms 升序合并；白名单外事件被过滤。"""
    rows = [
        {"kind": "danmaku", "ts_ms": 300, "content": "c"},
        {"kind": "gift_row", "ts_ms": 100, "gift_name": "g"},
    ]
    history = _FakeHistory(
        [
            _FakeRecord(type="planner.decision", timestamp_ms=200, data={"round_id": "r1"}),
            _FakeRecord(type="room.message.danmaku", timestamp_ms=250, data={}),  # 非白名单
        ]
    )
    items = build_timeline_items(rows, history, session_id=7, limit=50)
    assert [i["ts_ms"] for i in items] == [100, 200, 300]
    event = items[1]
    assert event["kind"] == "event" and event["event_type"] == "planner.decision"


def test_sort_stability_for_equal_ts() -> None:
    """ts_ms 相同的条目保持插入顺序（明细行在前、事件在后）。"""
    rows = [{"kind": "danmaku", "ts_ms": 100, "content": "a"}]
    history = _FakeHistory([_FakeRecord(type="streamer.stage", timestamp_ms=100, data={})])
    items = build_timeline_items(rows, history, session_id=1, limit=50)
    assert [i["kind"] for i in items] == ["danmaku", "event"]


def test_truncation_keeps_most_recent() -> None:
    """超出 limit 时保留最近（ts_ms 最大）的条目。"""
    rows = [{"kind": "danmaku", "ts_ms": ts, "content": str(ts)} for ts in range(10)]
    items = build_timeline_items(rows, None, session_id=1, limit=3)
    assert [i["ts_ms"] for i in items] == [7, 8, 9]
