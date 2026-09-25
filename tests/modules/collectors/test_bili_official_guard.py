"""bili_danmaku_official 采集器上舰（guard）事件形态测试

覆盖：
- GuardMessage 走 emit 路径发出 ``room.message.guard``（message_type="guard"）
- danmaku/gift/super_chat/enter 不被标为 guard（负向）
- 畸形上舰消息（缺字段/格式异常）不抛未捕获异常、记日志、优雅降级
"""

from __future__ import annotations

from typing import Any

from src.modules.collectors.bilibili.official.bili_danmaku_official_collector import (
    BiliDanmakuOfficialCollector,
)
from src.modules.events.names import CoreEvents

GUARD_CMD = "LIVE_OPEN_PLATFORM_GUARD"


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


def _guard_data(**extra: Any) -> dict:
    data = {
        "open_id": "guard_user_1",
        "uname": "舰长君",
        "guard_level": 3,
        "guard_num": 1,
        "guard_unit": "月",
        "price": 138,
        "timestamp": 1700000000,
    }
    data.update(extra)
    return {"cmd": GUARD_CMD, "data": data}


async def test_guard_message_emits_guard_event() -> None:
    """GuardMessage 走 emit 路径发出 room.message.guard，message_type=guard"""
    collector, bus = _make_collector()
    queue: Any = _FakeQueue()
    await collector._handle_message_from_bili(_guard_data(), queue)

    assert len(bus.events) == 1
    event_name, payload = bus.events[0]
    assert event_name == CoreEvents.ROOM_MESSAGE_GUARD
    assert event_name == "room.message.guard"
    assert payload.message_type == "guard"
    assert payload.user.id == "guard_user_1"
    assert payload.user.name == "舰长君"
    assert "舰长" in payload.content


async def test_non_guard_types_not_marked_guard() -> None:
    """danmaku/gift/super_chat/enter 均不被标为 guard、不发 guard 事件"""
    collector, bus = _make_collector()
    queue: Any = _FakeQueue()

    messages = [
        {"cmd": "LIVE_OPEN_PLATFORM_DM", "data": {"open_id": "u1", "uname": "观众", "msg": "你好", "timestamp": 1}},
        {
            "cmd": "LIVE_OPEN_PLATFORM_SEND_GIFT",
            "data": {
                "open_id": "u2",
                "uname": "礼物哥",
                "gift_name": "辣条",
                "gift_num": 1,
                "timestamp": 2,
                "combo_info": {"combo_count": 1},
            },
        },
        {
            "cmd": "LIVE_OPEN_PLATFORM_SUPER_CHAT",
            "data": {"open_id": "u3", "uname": "SC哥", "message": "冲", "rmb": 30, "timestamp": 3},
        },
        {"cmd": "LIVE_OPEN_PLATFORM_LIVE_ROOM_ENTER", "data": {"open_id": "u4", "uname": "路人", "timestamp": 4}},
    ]
    for msg in messages:
        await collector._handle_message_from_bili(msg, queue)

    assert len(bus.events) == 4
    assert all(name != CoreEvents.ROOM_MESSAGE_GUARD for name, _ in bus.events)
    assert all(payload.message_type != "guard" for _, payload in bus.events)
    assert [payload.message_type for _, payload in bus.events] == [
        "danmaku",
        "gift",
        "super_chat",
        "enter",
    ]


async def test_malformed_guard_message_dropped_without_identity() -> None:
    """畸形上舰消息：缺 data 字段 → open_id 缺失，告警丢弃不 emit（防多用户退化混淆）"""
    collector, bus = _make_collector()
    queue: Any = _FakeQueue()

    # data 缺用户字段：身份键缺失的消息不构造兜底载荷——空 user_id 会让
    # 多个无 id 用户在存储层退化成同一行，宁缺毋滥
    await collector._handle_message_from_bili({"cmd": GUARD_CMD, "data": {}}, queue)
    assert bus.events == []


async def test_malformed_guard_payload_type_error_swallowed() -> None:
    """畸形上舰消息：字段格式异常 → 顶层捕获记日志，不抛未捕获异常、不 emit"""
    collector, bus = _make_collector()
    queue: Any = _FakeQueue()

    # guard_level/price 为不可用类型时，消息处理路径应整体兜底（记日志后返回）
    await collector._handle_message_from_bili(
        {"cmd": GUARD_CMD, "data": {"open_id": {"bad": "type"}, "uname": ["x"], "timestamp": "abc"}},
        queue,
    )
    assert bus.events == []


class _FakeQueue:
    """替代 asyncio.Queue：只记录 put 的载荷。"""

    def __init__(self) -> None:
        self.items: list[Any] = []

    async def put(self, item: Any) -> None:
        self.items.append(item)
