"""VTSProvider：热键目录与片段登记、表情交给面捕合成器、面捕驱动挂载与收尾、不向 LLM 暴露工具、连接/断开/重连（假连接，不连真实 VTS）"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.modules.avatar.platform.vts.tracking_driver import VBRIDGER_INPUTS
from src.modules.avatar.platform.vts.vts_client import VtsClient
from src.modules.avatar.platform.vts.vts_provider import VTSProvider, create_vts_provider, register_vts_tools
from src.modules.avatar.tracking import TrackingConfig, TrackingRuntime
from src.modules.tools.models import ToolInvocation
from src.modules.tools.operations import ToolOperations
from src.modules.tools.registry import ToolRegistry
from src.modules.utils.backoff import ExponentialBackoff
from tests.config.helpers import wait_until
from tests.helpers import FakeClock
from tests.modules.avatar.platform.vts.fakes import FakeVtsSocket, socket_connector

HOTKEYS = [{"name": "Wave", "hotkeyID": "u1", "type": "TriggerAnimation"}, {"name": "Nod", "hotkeyID": "u2"}]


def _socket(model: str = "hiyori", hotkeys: Optional[List[Dict[str, Any]]] = None) -> FakeVtsSocket:
    socket = FakeVtsSocket(
        {
            "HotkeysInCurrentModelRequest": {"availableHotkeys": list(HOTKEYS if hotkeys is None else hotkeys)},
            "HotkeyTriggerRequest": lambda data: {"hotkeyID": data["hotkeyID"]},
            "InputParameterListRequest": {
                "defaultParameters": [
                    {"name": "FaceAngleX", "min": -30, "max": 30},
                    {"name": "EyeOpenLeft", "min": 0, "max": 1},
                    {"name": "MouthOpen", "min": 0, "max": 1},
                ]
            },
            "APIStateRequest": {"active": True},
        }
    )
    socket.model = model  # type: ignore[attr-defined]
    socket.replies["CurrentModelRequest"] = lambda data: {"modelLoaded": True, "modelName": socket.model}  # type: ignore[attr-defined]
    return socket


def _factory(tmp_path: Path, connector: Any) -> Any:
    def factory(**kwargs: Any) -> VtsClient:
        # 令牌写到临时目录：测试绝不读写真实的 data/vts_token.txt
        return VtsClient(**{**kwargs, "token_path": tmp_path / "vts_token.txt"}, connector=connector)

    return factory


def _provider(
    tmp_path: Path,
    socket: Optional[FakeVtsSocket] = None,
    *,
    tracking: Optional[TrackingRuntime] = None,
    connector=None,
) -> VTSProvider:
    connect = connector or socket_connector(socket or _socket())
    return create_vts_provider(config={}, tracking=tracking, client_factory=_factory(tmp_path, connect))


def _tracking(clock: FakeClock) -> TrackingRuntime:
    config = TrackingConfig(blink_enabled=False, gaze_enabled=False, head_wander_enabled=False)
    return TrackingRuntime(config, clock=clock.now_ms, rng=random.Random(0), run_loop=False)


def _injected(socket: FakeVtsSocket) -> List[Dict[str, float]]:
    return [
        {p["id"]: p["value"] for p in message["data"]["parameterValues"]}
        for message in socket.sent
        if message["messageType"] == "InjectParameterDataRequest"
    ]


async def test_connect_loads_hotkeys_but_exposes_no_llm_tools(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    fired: list[bool] = []
    provider.on_connection_changed = fired.append
    assert await provider.connect() is True
    assert fired == [True] and provider.list_tools() == []  # 形象动作统一走 avatar_act
    result = await provider.list_preset_actions()
    assert [a["name"] for a in result.structured_content["actions"]] == ["Wave", "Nod"]
    invoked = await provider.invoke(ToolInvocation(tool_name="vts_set_expression", arguments={}, source="test"))
    assert invoked.success is False  # 旧工具名已经不属于本适配器
    await provider.disconnect()
    await provider.disconnect()  # 已断开再断开不重复触发
    assert fired == [True, False] and provider.list_tools() == []


async def test_connect_creates_missing_vbridger_inputs_but_leaves_existing_ones(tmp_path: Path) -> None:
    socket = _socket()
    listing = dict(socket.replies["InputParameterListRequest"])
    listing["customParameters"] = [{"name": "JawOpen", "min": 0, "max": 1, "addedBy": "VBridger"}]  # 装过 VBridger
    socket.replies["InputParameterListRequest"] = listing
    socket.replies["ParameterCreationRequest"] = lambda data: {"parameterName": data["parameterName"]}
    provider = _provider(tmp_path, socket)
    await provider.connect()
    created = {
        m["data"]["parameterName"]: m["data"] for m in socket.sent if m["messageType"] == "ParameterCreationRequest"
    }
    assert set(created) == set(VBRIDGER_INPUTS) - {"JawOpen"}  # 别的插件建的不动，缺的按同名同范围补
    assert (created["MouthPucker"]["min"], created["MouthPucker"]["max"]) == (-1.0, 1.0)
    await provider.disconnect()


async def test_failed_connect_records_last_error(tmp_path: Path) -> None:
    async def refuse(url: str) -> Any:
        raise OSError("远程计算机拒绝网络连接")

    provider = _provider(tmp_path, connector=refuse)
    assert await provider.connect() is False
    assert "VTS 连接失败" in provider.last_error and "拒绝" in provider.last_error


async def test_trigger_preset_action_known_unknown_and_missing(tmp_path: Path) -> None:
    socket = _socket()
    provider = _provider(tmp_path, socket)
    await provider.connect()
    assert (await provider.trigger_preset_action("Wave")).success is True  # 调试台直调
    assert socket.sent[-1]["data"] == {"hotkeyID": "u1"}
    unknown = await provider.trigger_preset_action("不存在")
    assert unknown.success is False and "Wave" in unknown.structured_content["available_actions"]
    assert (await provider.trigger_preset_action("")).success is False
    assert await provider.trigger_hotkey(name="不存在", hotkey_id="u9") is True  # 名未命中时按 id 兜底
    assert socket.sent[-1]["data"] == {"hotkeyID": "u9"}
    assert await provider.trigger_hotkey() is False
    await provider.disconnect()


async def test_debug_set_expression_goes_to_tracking(tmp_path: Path, fake_clock: FakeClock) -> None:
    tracking = _tracking(fake_clock)
    provider = _provider(tmp_path, tracking=tracking)
    result = await provider.set_expression("happy", 0.7)
    assert result.success is True and result.structured_content["outcome"] == "started"
    assert tracking.current_emotion() == "happy"
    assert (await provider.set_expression("这不是情绪", 0.5)).success is False

    no_tracking = _provider(tmp_path)
    assert (await no_tracking.set_expression("happy", 0.5)).success is False  # 合成器未装配：如实失败


async def test_setup_attaches_driver_and_cleanup_leaves_rest_pose(tmp_path: Path, fake_clock: FakeClock) -> None:
    socket = _socket()
    tracking = _tracking(fake_clock)
    await tracking.start()
    provider = _provider(tmp_path, socket, tracking=tracking)
    await provider.setup()
    assert tracking.driver_names == ["vts"]
    clips = [a["name"] for a in tracking.action_catalog() if a["kind"] == "vts 片段"]
    assert clips == ["Wave", "Nod"]  # 热键登记为动作片段
    assert await tracking.act("Nod") is True and socket.sent[-1]["data"] == {"hotkeyID": "u2"}

    tracking.express("tired", 1.0)
    for _ in range(30):
        fake_clock.tick(33)
        await tracking.tick_once()
    frames = _injected(socket)
    assert frames and set(frames[-1]) == {"FaceAngleX", "EyeOpenLeft", "MouthOpen"}  # 只发 VTS 认识的输入
    assert frames[-1]["EyeOpenLeft"] < 0.4  # 困倦半闭眼

    assert await provider.set_parameter_value("MouthOpen", 0.9) is True  # 调试台临时覆盖
    await tracking.tick_once()
    assert _injected(socket)[-1]["MouthOpen"] == 0.9

    await provider.cleanup()
    assert _injected(socket)[-1]["EyeOpenLeft"] == pytest.approx(0.55)  # 断开前复原到静止姿态
    assert socket.closed and tracking.driver_names == []
    assert not [a for a in tracking.action_catalog() if a["kind"] == "vts 片段"]  # 片段随之摘下
    await tracking.stop()


async def test_dead_connection_is_detected_and_tools_removed(tmp_path: Path) -> None:
    socket = _socket()
    provider = VTSProvider(
        config={}, client_factory=_factory(tmp_path, socket_connector(socket)), reconnect_interval_s=0.01
    )
    fired: list[bool] = []
    provider.on_connection_changed = fired.append
    await provider.setup()
    assert await provider.health_check() is True
    await socket.drop()  # VTS 那头断开（比如被关掉）
    await wait_until(lambda: fired[-1:] == [False], what="巡检判死并摘除工具")
    assert await provider.health_check() is False
    await provider.cleanup()


async def test_model_switch_reloads_hotkeys_only_on_real_change(tmp_path: Path) -> None:
    socket = _socket()
    provider = _provider(tmp_path, socket)
    await provider.connect()
    for model, reloads in (("hiyori", 0), ("akari", 1), ("akari", 0)):
        before = socket.sent_types().count("HotkeysInCurrentModelRequest")
        socket.model = model  # type: ignore[attr-defined]
        await provider._follow_model_switch()
        assert socket.sent_types().count("HotkeysInCurrentModelRequest") - before == reloads
    await provider.disconnect()


def test_register_vts_tools_reports_connection_state_without_tools() -> None:
    registry = ToolRegistry()
    provider = register_vts_tools(registry, config={"vts_host": "localhost", "vts_port": 8001})

    def card() -> dict:
        return next(p for p in ToolOperations(registry).list_providers() if p["name"] == "vts")

    assert card()["tool_count"] == 0 and card()["connected"] is False  # 装配期未连接
    provider.mark_connected()
    assert registry.list_tools(provider="vts") == [] and card()["connected"] is True  # 连上也没有 LLM 工具
    provider.mark_disconnected()
    assert card()["connected"] is False


async def test_reconnect_attempts_back_off_and_reset_on_success(tmp_path: Path) -> None:
    """VTS 长关：重连尝试按指数间隔跳过巡检整点（压频不打日志风暴），连上即复位。"""
    socket = _socket()
    attempts = {"n": 0}

    async def flaky(url: str) -> FakeVtsSocket:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError("VTS 没开")
        return socket

    provider = _provider(tmp_path, connector=flaky)
    provider._reconnect_backoff = ExponentialBackoff(base_delay_ms=10_000, cap_delay_ms=60_000)
    await provider._attempt_reconnect()  # 尝试 1：失败 → 跳 2 拍（10s 退避 / 5s 节拍）
    assert provider._reconnect_skip_polls == 2
    await provider._attempt_reconnect()
    await provider._attempt_reconnect()
    assert attempts["n"] == 1  # 退避窗口内只消耗节拍，不触网
    await provider._attempt_reconnect()  # 尝试 2：失败 → 跳 4 拍
    assert provider._reconnect_skip_polls == 4 and attempts["n"] == 2
    for _ in range(4):
        await provider._attempt_reconnect()
    await provider._attempt_reconnect()  # 尝试 3：连上 → 复位
    assert attempts["n"] == 3 and provider._reconnect_skip_polls == 0
    assert provider._reconnect_backoff.next_delay_ms() == 10_000, "下一次失败从起点重新起算"
    await provider.disconnect()
