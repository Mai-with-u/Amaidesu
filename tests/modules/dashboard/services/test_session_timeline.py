"""session_timeline 服务层：合并排序 / 游标分页 / kind 映射 / 入参不可变。"""

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


def _danmaku(*stamps: int) -> List[Dict[str, Any]]:
    return [{"kind": "danmaku", "ts_ms": ts, "content": str(ts)} for ts in stamps]


def test_gift_and_super_chat_rows_map_to_card_kinds_without_mutating_the_input() -> None:
    rows = [
        {"kind": "gift_row", "ts_ms": 2, "gift_name": "小花花"},
        {"kind": "super_chat_row", "ts_ms": 1, "amount": 30.0},
        {"kind": "danmaku", "ts_ms": 3, "content": "你好"},
    ]

    items, has_more = build_timeline_items(rows, [], limit=50)

    assert [i["kind"] for i in items] == ["super_chat", "gift", "danmaku"] and has_more is False  # 其余 kind 直通
    assert rows[0]["kind"] == "gift_row"  # 构造新条目，不原地改写入参


def test_detail_rows_and_event_records_merge_by_ts_with_stable_order_for_ties() -> None:
    rows = [
        {"kind": "danmaku", "ts_ms": 300, "content": "c"},
        {"kind": "gift_row", "ts_ms": 100, "gift_name": "g"},
        {"kind": "danmaku", "ts_ms": 200, "content": "同刻明细"},
    ]
    records = [
        _FakeRecord(type="planner.decision", timestamp_ms=200, data={"round_id": "r1"}),
        _FakeRecord(type="tool.result.streamer_reply", timestamp_ms=250),
    ]

    items, _ = build_timeline_items(rows, records, limit=50)

    assert [i["ts_ms"] for i in items] == [100, 200, 200, 250, 300]
    assert [i["kind"] for i in items[1:3]] == ["danmaku", "event"]  # ts 相同：明细行在前、事件在后
    assert items[2]["event_type"] == "planner.decision"


def test_limit_keeps_the_most_recent_and_cursor_keeps_only_strictly_older_items() -> None:
    items, has_more = build_timeline_items(_danmaku(*range(10)), [], limit=3)
    assert [i["ts_ms"] for i in items] == [7, 8, 9] and has_more is True

    records = [_FakeRecord(type="planner.decision", timestamp_ms=200)]
    older, has_more = build_timeline_items(_danmaku(100, 200, 300), records, limit=50, before_timestamp_ms=200)
    assert [i["ts_ms"] for i in older] == [100] and has_more is False  # 向前翻页不重复


def test_event_payload_long_strings_are_trimmed_on_the_way_out() -> None:
    """防大工具结果撑爆响应。"""
    records = [_FakeRecord(type="tool.result.maicraft_observe", timestamp_ms=1, data={"result": "x" * 20_000})]

    items, _ = build_timeline_items([], records, limit=10)

    assert len(items[0]["data"]["result"]) < 9_000 and items[0]["data"]["result"].endswith("…[已截断]")
