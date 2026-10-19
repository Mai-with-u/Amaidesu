"""``bili_danmaku_laplace`` 采集器测试

覆盖：
- ConfigSchema 默认值与校验
- 五类 Laplace 事件 → ``RoomMessagePayload`` 全字段
- 过滤（room / uid / handle / deleted / action / toastType）
- 去重（stableKey / id、类型前缀、缓存淘汰）
- CoreEvents 映射与真实 EventBus 判别校验
- 本地假 bridge 端到端（established、ping/pong、坏 JSON、token query、断线重连）
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Callable, Dict, List, Tuple
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError
from websockets.asyncio.server import serve

from src.modules.collectors.bilibili.laplace.bili_danmaku_laplace_collector import (
    BiliDanmakuLaplaceCollector,
)
from src.modules.collectors.bilibili.laplace.client import LaplaceBridgeClient
from src.modules.collectors.bilibili.laplace.events import LaplaceEventMapper
from src.modules.collectors.factory import instantiate_collector
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload


class _FakeBus:
    """记录 emit 调用的最小 EventBus 替身（BaseCollector 会传 source=）。"""

    def __init__(self) -> None:
        self.events: List[Tuple[str, RoomMessagePayload]] = []

    async def emit(self, event_name: str, payload: RoomMessagePayload, source: str = "unknown") -> None:
        self.events.append((event_name, payload))


class _FakeClient:
    """按给定事件序列产出事件的客户端替身。"""

    def __init__(self, events: List[Dict[str, Any]]) -> None:
        self._events = events

    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        for event in self._events:
            yield event


def _base(**overrides: Any) -> Dict[str, Any]:
    event: Dict[str, Any] = {
        "origin": 3159986,
        "uid": 12345,
        "username": "观众A",
        "timestamp": 1700000000000000000,
        "timestampNormalized": 1700000000000,
        "medal": {"level": 5, "name": "牌子"},
    }
    event.update(overrides)
    return event


def _message_event(**overrides: Any) -> Dict[str, Any]:
    event = _base(type="message", id="evt-msg", message="主播好", idStr="dm-1")
    event.update(overrides)
    return event


def _gift_event(**overrides: Any) -> Dict[str, Any]:
    event = _base(
        type="gift",
        id="evt-gift",
        uid=222,
        username="礼物哥",
        price=1000,
        priceNormalized=1.0,
        coinType="gold",
        guardType=3,
        giftAmount=2,
        giftId=31036,
        giftName="小星星",
        blindGift=None,
    )
    event.update(overrides)
    return event


def _sc_event(**overrides: Any) -> Dict[str, Any]:
    event = _base(
        type="superchat",
        id="evt-sc",
        uid=333,
        username="SC哥",
        message="加油",
        price=30000,
        priceNormalized=30.0,
        duration=60,
        guardType=0,
        scId=9527,
        token="sc-secret-token",
        deleted=False,
    )
    event.update(overrides)
    return event


def _toast_event(**overrides: Any) -> Dict[str, Any]:
    event = _base(
        type="toast",
        id="evt-toast",
        uid=444,
        username="舰长酱",
        stableKey="toast-stable-1",
        price=138000,
        priceNormalized=138.0,
        toastType=3,
        toastAmount=1,
        toastAmountUnit="月",
        toastName="舰长",
        message="舰长酱 开通了舰长",
    )
    event.update(overrides)
    return event


def _interaction_event(**overrides: Any) -> Dict[str, Any]:
    event = _base(type="interaction", id="evt-enter", uid=555, username="路人", action=1)
    event.update(overrides)
    return event


async def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    """轮询等待条件成立（测试用；超时抛 AssertionError）。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等待条件超时")


# ===== ConfigSchema =====


def test_config_defaults() -> None:
    cfg = BiliDanmakuLaplaceCollector.ConfigSchema()
    assert cfg.url == "ws://localhost:9696"
    assert cfg.token == ""
    assert cfg.room_id == 0
    assert cfg.emit_semantic_events is True
    assert cfg.handle_enter_messages is True
    assert cfg.handle_gift_messages is True
    assert cfg.handle_superchat_messages is True
    assert cfg.handle_guard_messages is True
    assert cfg.dedupe_cache_size == 2000
    assert cfg.reconnect_base_ms == 3000
    assert cfg.reconnect_max_ms == 60000


def test_config_rejects_invalid_url() -> None:
    with pytest.raises(ValidationError):
        BiliDanmakuLaplaceCollector.ConfigSchema(url="http://localhost:9696")
    with pytest.raises(ValidationError):
        BiliDanmakuLaplaceCollector.ConfigSchema(url="ws://localhost:9696#frag")


def test_config_rejects_reconnect_range() -> None:
    with pytest.raises(ValidationError):
        BiliDanmakuLaplaceCollector.ConfigSchema(reconnect_base_ms=1000, reconnect_max_ms=500)


def test_factory_instantiates_with_empty_config() -> None:
    instance = instantiate_collector("bili_danmaku_laplace", {})
    assert isinstance(instance, BiliDanmakuLaplaceCollector)
    assert instance.name == "bili_danmaku_laplace"


# ===== 事件映射 =====


def test_message_payload() -> None:
    payload = LaplaceEventMapper().to_payload(_message_event())
    assert payload is not None
    assert payload.message_type == "danmaku"
    assert payload.platform == "bilibili"
    assert payload.user.id == "12345"
    assert payload.user.name == "观众A"
    assert payload.content == "主播好"
    assert payload.message_id == "dm-1"
    assert payload.timestamp_ms == 1700000000000


def test_gift_payload() -> None:
    payload = LaplaceEventMapper().to_payload(_gift_event())
    assert payload is not None and payload.gift is not None
    gift = payload.gift
    assert payload.message_type == "gift"
    assert payload.message_id == "evt-gift"
    assert gift.name == "小星星"
    assert gift.count == 2
    assert gift.gift_id == 31036
    assert gift.blind_gift_id == 0
    assert gift.unit_price == 500
    assert gift.total_price == 1000
    assert gift.paid_price == 1000
    assert gift.currency == "bilibili_gold_coin"
    assert gift.guard_level == 3
    assert gift.fans_medal_level == 5
    assert gift.fans_medal_name == "牌子"
    assert gift.msg_id == "evt-gift"
    assert gift.raw_data and "小星星" in gift.raw_data


def test_gift_silver_and_unknown_currency() -> None:
    mapper = LaplaceEventMapper()
    silver = mapper.to_payload(_gift_event(coinType="silver", price=0))
    assert silver is not None and silver.gift is not None
    assert silver.gift.currency == "bilibili_silver_coin"

    unknown = mapper.to_payload(_gift_event(id="evt-gift-2", coinType="mystery"))
    assert unknown is not None and unknown.gift is not None
    assert unknown.gift.currency == ""


def test_gift_blind_gift_config_id() -> None:
    payload = LaplaceEventMapper().to_payload(
        _gift_event(blindGift={"blind_gift_config_id": 32251, "original_gift_id": 31036})
    )
    assert payload is not None and payload.gift is not None
    assert payload.gift.blind_gift_id == 32251


def test_gift_blind_gift_legacy_fallback() -> None:
    payload = LaplaceEventMapper().to_payload(_gift_event(blindGift={"blindGiftId": 9001}))
    assert payload is not None and payload.gift is not None
    assert payload.gift.blind_gift_id == 9001


def test_gift_blind_gift_does_not_use_original_gift_id() -> None:
    payload = LaplaceEventMapper().to_payload(_gift_event(blindGift={"original_gift_id": 31036}))
    assert payload is not None and payload.gift is not None
    assert payload.gift.blind_gift_id == 0


def test_gift_non_blind_gift_has_zero_id() -> None:
    payload = LaplaceEventMapper().to_payload(_gift_event(blindGift=None))
    assert payload is not None and payload.gift is not None
    assert payload.gift.blind_gift_id == 0


def test_super_chat_payload() -> None:
    payload = LaplaceEventMapper().to_payload(_sc_event())
    assert payload is not None and payload.sc is not None
    sc = payload.sc
    assert payload.content == "加油"
    assert payload.message_id == "9527"
    assert sc.total_price == 30000
    assert sc.currency == "bilibili_gold_coin"
    assert sc.start_time == 1700000000
    assert sc.end_time == 1700000060
    assert sc.message_id == "9527"
    assert "sc-secret-token" not in sc.raw_data
    assert "***" in sc.raw_data


def test_super_chat_price_uses_normalized_currency() -> None:
    # 1000 元以上的 SC 若按 price 大小猜单位，会被误判成金瓜子并缩小 1000 倍
    payload = LaplaceEventMapper().to_payload(_sc_event(price=2_000, priceNormalized=2_000.0))
    assert payload is not None and payload.sc is not None
    assert payload.sc.total_price == 2_000_000


def test_super_chat_price_prefers_normalized_when_raw_is_gold() -> None:
    payload = LaplaceEventMapper().to_payload(_sc_event(price=2_000_000, priceNormalized=2_000.0))
    assert payload is not None and payload.sc is not None
    assert payload.sc.total_price == 2_000_000


def test_super_chat_price_falls_back_to_raw_price() -> None:
    payload = LaplaceEventMapper().to_payload(_sc_event(price=30_000, priceNormalized=0.0))
    assert payload is not None and payload.sc is not None
    assert payload.sc.total_price == 30_000


def test_guard_payload() -> None:
    payload = LaplaceEventMapper().to_payload(_toast_event())
    assert payload is not None and payload.guard is not None
    guard = payload.guard
    assert payload.message_type == "guard"
    assert payload.content == "舰长酱 开通了舰长"
    assert payload.message_id == "toast-stable-1"
    assert guard.guard_level == 3
    assert guard.guard_num == 1
    assert guard.guard_unit == "月"
    assert guard.total_price == 138000
    assert guard.currency == "bilibili_gold_coin"
    assert guard.msg_id == "toast-stable-1"
    assert guard.raw_data


def test_guard_content_falls_back_when_template_marker() -> None:
    payload = LaplaceEventMapper().to_payload(_toast_event(message="<%name%> 开通了舰长"))
    assert payload is not None
    assert "<%" not in payload.content
    assert "舰长酱" in payload.content


def test_enter_payload() -> None:
    payload = LaplaceEventMapper().to_payload(_interaction_event())
    assert payload is not None
    assert payload.message_type == "enter"
    assert payload.user.id == "555"
    assert payload.content == ""


# ===== 过滤与去重 =====


def test_negative_uid_is_kept() -> None:
    payload = LaplaceEventMapper().to_payload(_message_event(uid=-12345))
    assert payload is not None
    assert payload.user.id == "-12345"


def test_zero_uid_is_dropped() -> None:
    assert LaplaceEventMapper().to_payload(_message_event(uid=0)) is None


def test_room_filter() -> None:
    mapper = LaplaceEventMapper(room_id=3159986)
    assert mapper.to_payload(_message_event(origin=1)) is None
    assert mapper.to_payload(_message_event(origin=3159986)) is not None


def test_deleted_superchat_dropped() -> None:
    assert LaplaceEventMapper().to_payload(_sc_event(deleted=True)) is None


def test_non_guard_toast_dropped() -> None:
    assert LaplaceEventMapper().to_payload(_toast_event(toastType=0)) is None


def test_non_enter_interaction_dropped() -> None:
    assert LaplaceEventMapper().to_payload(_interaction_event(action=2)) is None


def test_unknown_type_dropped() -> None:
    assert LaplaceEventMapper().to_payload({"type": "online-update", "uid": 1}) is None


def test_handle_switches() -> None:
    mapper = LaplaceEventMapper(
        handle_enter=False,
        handle_gift=False,
        handle_superchat=False,
        handle_guard=False,
    )
    assert mapper.to_payload(_gift_event()) is None
    assert mapper.to_payload(_sc_event()) is None
    assert mapper.to_payload(_toast_event()) is None
    assert mapper.to_payload(_interaction_event()) is None
    assert mapper.to_payload(_message_event()) is not None


def test_duplicate_message_dropped() -> None:
    mapper = LaplaceEventMapper()
    assert mapper.to_payload(_message_event()) is not None
    assert mapper.to_payload(_message_event()) is None


def test_same_id_different_types_not_cross_deduped() -> None:
    mapper = LaplaceEventMapper()
    assert mapper.to_payload(_message_event(id="same", idStr="")) is not None
    assert mapper.to_payload(_gift_event(id="same")) is not None


def test_toast_stable_key_dedupe() -> None:
    mapper = LaplaceEventMapper()
    assert mapper.to_payload(_toast_event(id="a", stableKey="same-key")) is not None
    assert mapper.to_payload(_toast_event(id="b", stableKey="same-key")) is None


def test_dedupe_cache_eviction() -> None:
    mapper = LaplaceEventMapper(dedupe_cache_size=1)
    assert mapper.to_payload(_message_event(id="1", idStr="1")) is not None
    assert mapper.to_payload(_message_event(id="2", idStr="2")) is not None
    assert mapper.to_payload(_message_event(id="1", idStr="1")) is not None


# ===== EventBus 集成 =====


async def test_emit_maps_to_core_event() -> None:
    bus = _FakeBus()
    collector = BiliDanmakuLaplaceCollector(config={}, event_bus=bus)
    payload = collector._mapper.to_payload(_message_event())
    assert payload is not None
    await collector._emit_semantic_event(payload)
    assert bus.events == [(CoreEvents.ROOM_MESSAGE_DANMAKU, payload)]


async def test_real_event_bus_discriminant() -> None:
    bus = EventBus(enable_stats=False)
    received: List[RoomMessagePayload] = []

    async def handler(event_name: str, data: RoomMessagePayload, source: str) -> None:
        received.append(data)

    bus.on(CoreEvents.ROOM_MESSAGE_DANMAKU, handler, RoomMessagePayload)
    collector = BiliDanmakuLaplaceCollector(config={}, event_bus=bus)
    payload = collector._mapper.to_payload(_message_event())
    assert payload is not None
    await collector._emit_semantic_event(payload)
    await _wait_until(lambda: bool(received))
    assert received[0].message_type == "danmaku"
    await bus.cleanup()


async def test_emit_disabled_still_yields() -> None:
    bus = _FakeBus()
    collector = BiliDanmakuLaplaceCollector(config={"emit_semantic_events": False}, event_bus=bus)
    collector._client = _FakeClient([_message_event()])  # type: ignore[assignment]

    payloads: List[RoomMessagePayload] = []
    async for payload in collector.collect():
        payloads.append(payload)
        break

    assert len(payloads) == 1
    assert payloads[0].message_type == "danmaku"
    assert bus.events == []


# ===== 客户端重连退避 =====


def test_reconnect_delay_caps_without_overflow() -> None:
    client = LaplaceBridgeClient(
        url="ws://localhost:9696",
        token="",
        reconnect_base_ms=3000,
        reconnect_max_ms=60000,
    )
    assert client._reconnect_delay_ms(1) == 3000
    assert client._reconnect_delay_ms(2) == 4500
    assert client._reconnect_delay_ms(10_000) == 60000


# ===== 本地假 bridge 端到端 =====


async def test_bridge_end_to_end_and_token_query() -> None:
    bus = _FakeBus()
    handshakes: List[Tuple[str, str]] = []

    async def handler(websocket: Any) -> None:
        handshakes.append(
            (
                websocket.request.headers.get("Sec-WebSocket-Protocol", ""),
                websocket.request.path,
            )
        )
        await websocket.send(
            json.dumps({"type": "established", "clientId": "client-1", "isServer": False, "message": "ok"})
        )
        await websocket.send(json.dumps(_message_event()))
        await asyncio.sleep(0.2)

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        collector = BiliDanmakuLaplaceCollector(
            config={
                "url": f"ws://127.0.0.1:{port}",
                "token": "abc=def/ghi",
                "reconnect_base_ms": 50,
                "reconnect_max_ms": 1000,
            },
            event_bus=bus,
        )
        await collector.start()
        try:
            await _wait_until(lambda: bool(bus.events) and bool(handshakes))
        finally:
            await collector.stop()
            await collector.cleanup()

    assert bus.events[0][0] == CoreEvents.ROOM_MESSAGE_DANMAKU
    assert bus.events[0][1].message_id == "dm-1"

    header, path = handshakes[0]
    assert "abc=def/ghi" not in header
    query = parse_qs(urlsplit(path).query)
    assert query["token"] == ["abc=def/ghi"]


async def test_bridge_handles_ping_and_bad_json() -> None:
    bus = _FakeBus()
    pongs: List[Dict[str, Any]] = []

    async def handler(websocket: Any) -> None:
        await websocket.send(json.dumps({"type": "established", "clientId": "client-1"}))
        await websocket.send("not-json")
        await websocket.send(json.dumps({"type": "ping", "timestamp": 123}))
        pongs.append(json.loads(await asyncio.wait_for(websocket.recv(), timeout=2)))
        await websocket.send(json.dumps(_message_event()))
        await asyncio.sleep(0.2)

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        collector = BiliDanmakuLaplaceCollector(
            config={"url": f"ws://127.0.0.1:{port}", "reconnect_base_ms": 50, "reconnect_max_ms": 1000},
            event_bus=bus,
        )
        await collector.start()
        try:
            await _wait_until(lambda: bool(bus.events))
        finally:
            await collector.stop()
            await collector.cleanup()

    assert pongs and pongs[0]["type"] == "pong"
    assert pongs[0]["respondingTo"] == 123
    assert bus.events[0][1].content == "主播好"


async def test_bridge_reconnects_after_disconnect() -> None:
    bus = _FakeBus()
    connections = 0

    async def handler(websocket: Any) -> None:
        nonlocal connections
        connections += 1
        await websocket.send(json.dumps({"type": "established", "clientId": f"client-{connections}"}))
        if connections == 1:
            await websocket.send(json.dumps(_message_event(id="evt-1", idStr="dm-1")))
            await websocket.close()
            return
        await websocket.send(json.dumps(_message_event(id="evt-2", idStr="dm-2")))
        await asyncio.sleep(0.2)

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        collector = BiliDanmakuLaplaceCollector(
            config={"url": f"ws://127.0.0.1:{port}", "reconnect_base_ms": 50, "reconnect_max_ms": 1000},
            event_bus=bus,
        )
        await collector.start()
        try:
            await _wait_until(lambda: len(bus.events) >= 2)
        finally:
            await collector.stop()
            await collector.cleanup()

    assert connections >= 2
    assert [payload.message_id for _, payload in bus.events[:2]] == ["dm-1", "dm-2"]
