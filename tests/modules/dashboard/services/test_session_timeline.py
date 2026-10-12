"""session_timeline 服务层单测：合并排序 / 游标分页 / kind 映射 / 入参不可变。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from src.modules.dashboard.services.session_timeline import build_timeline_items


@dataclass
class _FakeRecord:
    """EventRecord 的最小替身（时间线只读 type / timestamp_ms / data）。"""

    type: str
    timestamp_ms: int
    data: Dict[str, Any] = field(default_factory=dict)


def test_kind_mapping_gift_and_super_chat() -> None:
    """gift_row/super_chat_row 映射为卡片 kind；其余 kind 直通。"""
    rows = [
        {"kind": "gift_row", "ts_ms": 2, "gift_name": "小花花"},
        {"kind": "super_chat_row", "ts_ms": 1, "amount": 30.0},
        {"kind": "danmaku", "ts_ms": 3, "content": "你好"},
    ]
    items, has_more = build_timeline_items(rows, [], limit=50)
    assert [i["kind"] for i in items] == ["super_chat", "gift", "danmaku"]
    assert has_more is False
    # 事件记录缺省时不产生 event 条目
    assert all(i["kind"] != "event" for i in items)


def test_input_rows_not_mutated() -> None:
    """kind 映射构造新条目，不原地改写入参。"""
    rows: List[Dict[str, Any]] = [{"kind": "gift_row", "ts_ms": 1, "gift_name": "辣条"}]
    build_timeline_items(rows, [], limit=10)
    assert rows[0]["kind"] == "gift_row"


def test_merge_sort_with_event_records() -> None:
    """明细行与事件记录按 ts_ms 升序合并为统一条目流。"""
    rows = [
        {"kind": "danmaku", "ts_ms": 300, "content": "c"},
        {"kind": "gift_row", "ts_ms": 100, "gift_name": "g"},
    ]
    records = [
        _FakeRecord(type="planner.decision", timestamp_ms=200, data={"round_id": "r1"}),
        _FakeRecord(type="tool.result.streamer_reply", timestamp_ms=250, data={}),
    ]
    items, _ = build_timeline_items(rows, records, limit=50)
    assert [i["ts_ms"] for i in items] == [100, 200, 250, 300]
    decision = items[1]
    assert decision["kind"] == "event" and decision["event_type"] == "planner.decision"


def test_sort_stability_for_equal_ts() -> None:
    """ts_ms 相同的条目保持插入顺序（明细行在前、事件在后）。"""
    rows = [{"kind": "danmaku", "ts_ms": 100, "content": "a"}]
    records = [_FakeRecord(type="streamer.stage", timestamp_ms=100, data={})]
    items, _ = build_timeline_items(rows, records, limit=50)
    assert [i["kind"] for i in items] == ["danmaku", "event"]


def test_truncation_keeps_most_recent_and_reports_more() -> None:
    """超出 limit 时保留最近（ts_ms 最大）的条目并置 has_more。"""
    rows = [{"kind": "danmaku", "ts_ms": ts, "content": str(ts)} for ts in range(10)]
    items, has_more = build_timeline_items(rows, [], limit=3)
    assert [i["ts_ms"] for i in items] == [7, 8, 9]
    assert has_more is True


def test_cursor_keeps_only_strictly_older_items() -> None:
    """游标只保留严格早于该时刻的条目（向前翻页不重复）。"""
    rows = [{"kind": "danmaku", "ts_ms": ts, "content": str(ts)} for ts in (100, 200, 300)]
    records = [_FakeRecord(type="planner.decision", timestamp_ms=200, data={})]
    items, has_more = build_timeline_items(rows, records, limit=50, before_timestamp_ms=200)
    assert [i["ts_ms"] for i in items] == [100]
    assert has_more is False


def test_event_data_trims_long_strings() -> None:
    """事件载荷超长字符串在返回侧截断（防大工具结果撑爆响应）。"""
    records = [_FakeRecord(type="tool.result.maicraft_observe", timestamp_ms=1, data={"result": "x" * 20_000})]
    items, _ = build_timeline_items([], records, limit=10)
    trimmed = items[0]["data"]["result"]
    assert len(trimmed) < 9_000
    assert trimmed.endswith("…[已截断]")
