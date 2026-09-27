"""bili_danmaku_official 采集器观看数（watched_change）事件形态测试

覆盖：
- WATCHED_CHANGE 走状态推送路径发出 ``room.state.watched_count``，
  平台常量盖章、audience_total 映射、不入行为流队列
- 配置关闭（handle_watched_change_messages / emit_semantic_events）时不 emit
- 畸形推送（缺 data 段 / 字段格式异常）不抛未捕获异常、不 emit
"""

from __future__ import annotations

from typing import Any

from src.modules.collectors.bilibili.official.bili_danmaku_official_collector import (
    BiliDanmakuOfficialCollector,
)
from src.modules.events.names import CoreEvents

WATCHED_CMD = "LIVE_OPEN_PLATFORM_WATCHED_CHANGE"


def _make_collector(**overrides: Any) -> tuple[BiliDanmakuOfficialCollector, _FakeBus]:
    config = {
        "id_code": "x",
        "app_id": "y",
        "access_key": "z",
        "access_key_secret": "w",
    }
    config.update(overrides)
    bus = _FakeBus()
    collector = BiliDanmakuOfficialCollector(config=config, event_bus=bus)
    return collector, bus


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    async def emit(self, event_name: str, payload, **kwargs):
        self.events.append((event_name, payload))


class _FakeQueue:
    """替代 asyncio.Queue：只记录 put 的载荷。"""

    def __init__(self) -> None:
        self.items: list[Any] = []

    async def put(self, item: Any) -> None:
        self.items.append(item)


def _watched_data(**extra: Any) -> dict:
    data = {
        "channel_id": "123456",
        "watched_count": 12000,
        "watched_show": "1.2万人看过",
    }
    data.update(extra)
    return {"cmd": WATCHED_CMD, "data": data}


async def test_watched_change_emits_state_event() -> None:
    """WATCHED_CHANGE 发出 room.state.watched_count，平台盖章 + 数值映射"""
    collector, bus = _make_collector()
    queue: Any = _FakeQueue()
    await collector._handle_message_from_bili(_watched_data(), queue)

    assert len(bus.events) == 1
    event_name, payload = bus.events[0]
    assert event_name == CoreEvents.ROOM_STATE_WATCHED_COUNT
    assert event_name == "room.state.watched_count"
    assert payload.platform == "bilibili"
    assert payload.audience_total == 12000
    assert payload.watched_show == "1.2万人看过"


async def test_watched_change_not_in_behavior_queue() -> None:
    """观看数是状态推送非行为流：不进 collect() 消息队列"""
    collector, _ = _make_collector()
    queue: Any = _FakeQueue()
    await collector._handle_message_from_bili(_watched_data(), queue)
    assert queue.items == []


async def test_watched_change_disabled_via_config() -> None:
    """handle_watched_change_messages=false → should_handle 拦截，不 emit"""
    collector, bus = _make_collector(handle_watched_change_messages=False)
    queue: Any = _FakeQueue()
    await collector._handle_message_from_bili(_watched_data(), queue)
    assert bus.events == []


async def test_watched_change_disabled_via_semantic_gate() -> None:
    """emit_semantic_events=false → 状态事件同样被门控"""
    collector, bus = _make_collector(emit_semantic_events=False)
    queue: Any = _FakeQueue()
    await collector._handle_message_from_bili(_watched_data(), queue)
    assert bus.events == []


async def test_malformed_watched_change_without_data_dropped() -> None:
    """缺 data 段的畸形推送：跳过不 emit（状态覆写不可逆，不用 0 冒充观测）"""
    collector, bus = _make_collector()
    queue: Any = _FakeQueue()
    await collector._handle_message_from_bili({"cmd": WATCHED_CMD}, queue)
    assert bus.events == []


async def test_malformed_watched_change_bad_type_swallowed() -> None:
    """字段格式异常：解析失败告警丢弃，不抛未捕获异常、不 emit"""
    collector, bus = _make_collector()
    queue: Any = _FakeQueue()
    await collector._handle_message_from_bili(
        {"cmd": WATCHED_CMD, "data": {"watched_count": "not-a-number"}},
        queue,
    )
    assert bus.events == []
