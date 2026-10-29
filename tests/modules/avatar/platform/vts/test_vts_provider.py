"""VTSProvider：热键与预设目录、情绪表情、连接/断开/重连、换模跟随与注册表换血（假连接，不连真实 VTS）"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from unittest.mock import AsyncMock, MagicMock

from src.modules.avatar.platform.vts.vts_client import VtsClient
from src.modules.avatar.platform.vts.vts_provider import VTSProvider, create_vts_provider, register_vts_tools
from src.modules.tools.models import ToolInvocation
from src.modules.tools.operations import ToolOperations
from src.modules.tools.registry import ToolRegistry
from src.modules.types.emotion_vocab import Emotion
from src.modules.utils.backoff import ExponentialBackoff
from tests.config.helpers import wait_until
from tests.modules.avatar.platform.vts.fakes import FakeVtsSocket, socket_connector

VTS_TOOLS = {"vts_set_expression", "vts_list_preset_actions", "vts_trigger_preset_action", "vts_set_idle_enabled"}
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


def _provider(tmp_path: Path, socket: Optional[FakeVtsSocket] = None, *, connector=None) -> VTSProvider:
    connect = connector or socket_connector(socket or _socket())
    return create_vts_provider(config={}, client_factory=_factory(tmp_path, connect))


async def _invoke(provider: VTSProvider, tool: str, **arguments: Any):
    return await provider.invoke(ToolInvocation(tool_name=tool, arguments=arguments, source="test"))


async def test_connect_loads_hotkeys_and_tools_follow_connection(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    assert provider.list_tools() == []  # 装配期未连接：降级登记 0 工具
    fired: list[bool] = []
    provider.on_connection_changed = fired.append
    assert await provider.connect() is True
    assert fired == [True] and {s.full_name for s in provider.list_tools()} == VTS_TOOLS
    result = await provider.list_preset_actions()
    assert [a["name"] for a in result.structured_content["actions"]] == ["Wave", "Nod"]
    expression = next(s for s in provider.list_tools() if s.full_name == "vts_set_expression")
    assert "Wave" not in expression.description  # 描述不承载目录，目录现取
    await provider.disconnect()
    await provider.disconnect()  # 已断开再断开不重复触发
    assert fired == [True, False] and provider.list_tools() == []


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
    assert (await _invoke(provider, "vts_trigger_preset_action", action="Wave")).success is True
    assert socket.sent[-1]["data"] == {"hotkeyID": "u1"}
    unknown = await _invoke(provider, "vts_trigger_preset_action", action="不存在")
    assert unknown.success is False and "Wave" in unknown.structured_content["available_actions"]
    assert (await _invoke(provider, "vts_trigger_preset_action")).success is False
    assert await provider.trigger_hotkey(name="不存在", hotkey_id="u9") is True  # 名未命中时按 id 兜底
    assert socket.sent[-1]["data"] == {"hotkeyID": "u9"}
    assert await provider.trigger_hotkey() is False
    await provider.disconnect()


async def test_set_expression_scales_by_intensity_and_rejects_unknown_emotion(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    provider.expression = MagicMock()
    provider.expression.set_multi_parameter = AsyncMock(return_value=True)

    assert (await _invoke(provider, "vts_set_expression", emotion="happy", intensity=0.5)).success is True
    assert provider.expression.set_multi_parameter.await_args.args[0] == {"MouthSmile": 0.4, "Brows": 0.3}
    assert (await _invoke(provider, "vts_set_expression", emotion="这不是情绪")).success is False
    assert set(provider._emotion_map) == {e.value for e in Emotion}  # 映射表覆盖词表全部 17 值


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


def test_register_vts_tools_registers_degraded_then_follows_connection_state() -> None:
    registry = ToolRegistry()
    provider = register_vts_tools(registry, config={"vts_host": "localhost", "vts_port": 8001})
    card = next(p for p in ToolOperations(registry).list_providers() if p["name"] == "vts")
    assert card["tool_count"] == 0 and registry.list_tools(provider="vts") == []
    provider.mark_connected()
    assert {s.full_name for s in registry.list_tools(provider="vts")} == VTS_TOOLS
    provider.mark_disconnected()
    assert registry.list_tools(provider="vts") == []


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
