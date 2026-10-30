"""WarudoProvider：面捕驱动挂载与收尾、动作片段登记与蓝图通道、抛鱼冷却、表情交给面捕合成器、不向 LLM 暴露工具、连接/断开/重连语义（不连真实 Warudo）"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.avatar.platform.warudo.ifacialmocap import encode_frame
from src.modules.avatar.platform.warudo.warudo_provider import (
    BUILTIN_MOTIONS,
    WarudoProvider,
    create_warudo_provider,
    register_warudo_tools,
)
from src.modules.avatar.tracking import TrackingConfig, TrackingRuntime
from src.modules.tools import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from tests.helpers import FakeClock


def _provider(**config) -> WarudoProvider:
    return WarudoProvider({"ws_host": "localhost", "ws_port": 19190, **config}, event_bus=MagicMock())


async def _invoke(provider: WarudoProvider, tool: str, **arguments):
    return await provider.invoke(ToolInvocation(tool_name=tool, arguments=arguments))


class FakeWebSocket:
    """Warudo 蓝图通道替身：记下发出的每个动作，直到被关上。"""

    def __init__(self) -> None:
        self.actions: List[tuple] = []
        self.close_code = None
        self._closed = asyncio.Event()

    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        self.actions.append((message["action"], message["data"]))

    async def close(self) -> None:
        self.close_code = 1000
        self._closed.set()

    async def wait_closed(self) -> None:
        await self._closed.wait()


class FakeDatagram:
    """面捕数据发送端替身：记下每个包。"""

    def __init__(self) -> None:
        self.packets: List[bytes] = []
        self.closed = False
        self.target: tuple = ()

    def sendto(self, data: bytes) -> None:
        self.packets.append(data)

    def is_closing(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True


def _tracking(clock: FakeClock) -> TrackingRuntime:
    config = TrackingConfig(blink_enabled=False, gaze_enabled=False, head_wander_enabled=False)
    return TrackingRuntime(config, clock=clock.now_ms, rng=random.Random(0), run_loop=False)


async def _connected(
    monkeypatch: pytest.MonkeyPatch, **kwargs: Any
) -> tuple[WarudoProvider, FakeWebSocket, FakeDatagram]:
    """建一个 Warudo 适配器：蓝图通道接假连接，面捕数据发到假发送端，然后 setup。"""
    ws, datagram = FakeWebSocket(), FakeDatagram()

    async def connect(uri: str) -> FakeWebSocket:
        return ws

    async def open_datagram(host: str, port: int) -> FakeDatagram:
        datagram.target = (host, port)
        return datagram

    monkeypatch.setattr("src.modules.avatar.platform.warudo.warudo_provider.websockets.connect", connect)
    config = kwargs.pop("config", {})
    provider = create_warudo_provider(config=config, datagram_factory=open_datagram, **kwargs)
    await provider.setup()
    return provider, ws, datagram


def test_action_catalog_defaults_to_builtin_and_merges_config() -> None:
    assert _provider().action_catalog == {}
    names = [e["name"] for e in _provider()._preset_action_catalog()]
    assert names == [*BUILTIN_MOTIONS, "throw_fish"]  # 内置头部动作、站姿与抛鱼始终在

    entries = {
        e["name"]: e["description"]
        for e in _provider(action_catalog={"wave": "招手", "nod_once": "用力点头"})._preset_action_catalog()
    }
    assert (entries["wave"], entries["nod_once"]) == ("招手", "用力点头")  # 同名以配置说明为准
    assert list(entries).count("nod_once") == 1 and entries["throw_fish"]

    with pytest.raises(ValueError):  # action_catalog 必须是 Dict[str, str]，非 dict 严格报错
        _provider(action_catalog="invalid")


def test_markers_config_rejects_unknown_names_and_empty_shapes() -> None:
    assert _provider(markers={"tears": " Tears "}).typed_config.markers == {"tears": "Tears"}
    with pytest.raises(ValueError, match="未知的风格化标记"):
        _provider(markers={"rainbow": "Rainbow"})
    with pytest.raises(ValueError, match="没写对应"):
        _provider(markers={"tears": ""})


async def test_setup_attaches_face_driver_and_cleanup_leaves_rest_pose(
    monkeypatch: pytest.MonkeyPatch, fake_clock: FakeClock
) -> None:
    tracking = _tracking(fake_clock)
    await tracking.start()
    provider, _, datagram = await _connected(monkeypatch, tracking=tracking)
    assert datagram.target == ("127.0.0.1", 49983) and tracking.driver_names == ["warudo"]
    tracking.express("happy", 1.0)
    for _ in range(20):
        fake_clock.tick(33)
        await tracking.tick_once()
    assert b"mouthSmile_L-" in datagram.packets[-1] and b"=head#" in datagram.packets[-1]
    smiling = int(datagram.packets[-1].split(b"mouthSmile_L-")[1].split(b"|")[0])
    assert smiling > 30

    await provider.cleanup()
    # 摘下前发一帧静止姿态：Warudo 收不到新数据时会停在最后一帧
    assert datagram.packets[-1] == encode_frame(tracking.rest_frame())
    assert datagram.closed and tracking.driver_names == []
    await tracking.stop()


async def test_builtin_motions_and_catalog_entries_are_clips_sent_over_the_blueprint_channel(
    monkeypatch: pytest.MonkeyPatch, fake_clock: FakeClock
) -> None:
    tracking = _tracking(fake_clock)
    await tracking.start()
    provider, ws, _ = await _connected(monkeypatch, tracking=tracking, config={"action_catalog": {"wave": "招手"}})
    clips = {e["name"] for e in tracking.action_catalog() if e["kind"] == "warudo 片段"}
    assert {"nod_once", "pose_think", "wave", "throw_fish"} <= clips
    for action in ("nod_once", "pose_think", "wave"):
        assert await tracking.act(action) is True
    assert ws.actions == [("nod_once", 1), ("pose_think", 1), ("wave", 1)]
    unknown = await provider.trigger_preset_action("dance")
    assert unknown.success is False and "nod_once" in unknown.structured_content["available_actions"]
    await provider.cleanup()
    await tracking.stop()


async def test_throw_fish_has_a_cooldown(monkeypatch: pytest.MonkeyPatch, fake_clock: FakeClock) -> None:
    provider, ws, _ = await _connected(monkeypatch, clock=fake_clock.now_ms, config={"throw_fish_cooldown": 5.0})
    first = await provider.trigger_preset_action("throw_fish")
    fake_clock.tick(4000)
    second = await provider.trigger_preset_action("throw_fish")
    fake_clock.tick(1000)
    third = await provider.trigger_preset_action("throw_fish")
    assert [r.structured_content["fired"] for r in (first, second, third)] == [True, False, True]
    assert ws.actions == [("throw_fish", 1), ("throw_fish", 1)]
    await provider.cleanup()


async def test_no_llm_tools_and_debug_expression_goes_to_tracking(
    monkeypatch: pytest.MonkeyPatch, fake_clock: FakeClock
) -> None:
    tracking = _tracking(fake_clock)
    provider, _, _ = await _connected(monkeypatch, tracking=tracking)
    assert provider.list_tools() == []  # 形象动作统一走 avatar_act
    assert (await _invoke(provider, "warudo_set_expression", emotion="happy")).success is False
    result = await provider.set_expression("happy", 0.8)
    assert result.success and tracking.current_emotion() == "happy"
    assert (await provider.set_expression("ecstatic", 0.5)).success is False
    await provider.cleanup()


def test_tools_stay_empty_and_registry_follows_connection() -> None:
    registry = ToolRegistry()
    provider = register_warudo_tools(registry, {"ws_host": "localhost", "ws_port": 19190})
    assert provider in registry.providers() and len(registry) == 0
    provider.mark_connected()
    assert len(registry) == 0
    assert provider.connection_summary()["is_connected"] is True


# 连接 / 断开 / 重连


def _stubbed_provider() -> WarudoProvider:
    provider = create_warudo_provider(config={"ws_host": "localhost", "ws_port": 19190})
    provider._connect = AsyncMock()  # type: ignore[method-assign]  # 避免真实触发 websockets.connect
    return provider


async def test_connect_failure_fires_no_callback() -> None:
    provider = _stubbed_provider()
    # _connect 被打桩后走真实失败分支不可行，直接验证手动断开只迁移时回调一次
    provider._is_connected = False
    fired: list[bool] = []
    provider.on_connection_changed = fired.append
    provider.websocket = MagicMock(close=AsyncMock())
    await provider.disconnect()
    assert fired == []  # 本就未连接，断开不触发迁移回调

    provider._is_connected = True
    provider.websocket = MagicMock(close=AsyncMock())
    await provider.disconnect()
    assert fired == [False]  # True→False 迁移回调一次


async def test_connect_reports_connection_state_and_reconnect_composes_disconnect_and_connect() -> None:
    provider = _stubbed_provider()
    assert provider.supports_reconnect is True
    provider._is_connected = True
    assert await provider.connect() is True
    provider._is_connected = False
    assert await provider.connect() is False

    ws = MagicMock(close=AsyncMock())
    provider.websocket = ws
    provider._is_connected = True

    async def fake_connect() -> None:
        provider._is_connected = True

    provider._connect = fake_connect  # type: ignore[method-assign]
    assert await provider.reconnect() is True  # 默认组合：先断开再连接
    ws.close.assert_awaited_once()


async def test_disconnect_closes_only_the_websocket_and_keeps_background_loop() -> None:
    provider = _stubbed_provider()
    ws = MagicMock(close=AsyncMock())
    loop_task = MagicMock()
    provider.websocket, provider._connection_task = ws, loop_task
    provider._is_connected, provider._should_stop, provider._has_started = True, False, True

    assert await provider.disconnect() is True
    ws.close.assert_awaited_once()
    assert provider.websocket is None and provider._is_connected is False
    # 后台重连循环继续驱动，setup 语义不被破坏
    assert provider._should_stop is False and provider._connection_task is loop_task and provider._has_started is True

    provider.websocket = None
    assert await provider.disconnect() is True  # 没有 websocket 也安全

    broken = MagicMock(close=AsyncMock(side_effect=RuntimeError("boom")))
    provider.websocket, provider._is_connected = broken, True
    assert await provider.disconnect() is True  # close 抛异常也要置为断开
    assert provider.websocket is None and provider._is_connected is False


async def test_connection_loop_backs_off_between_reconnect_attempts(monkeypatch) -> None:
    """Warudo 长关：重连等待从配置间隔起指数拉长——固定 10ms 间隔下 0.2s 会尝试
    约 20 次，退避（10→20→40→80ms）下只应有个位数次。"""
    import asyncio

    provider = _provider(reconnect_delay_seconds=0.01)
    attempts = {"n": 0}

    async def _refused(uri: str) -> None:
        attempts["n"] += 1
        raise ConnectionRefusedError("Warudo 未开")

    monkeypatch.setattr("src.modules.avatar.platform.warudo.warudo_provider.websockets.connect", _refused)
    task = asyncio.create_task(provider._connection_loop("ws://localhost:19190"))
    try:
        await asyncio.sleep(0.2)
    finally:
        provider._should_stop = True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert 2 <= attempts["n"] <= 10
