"""OBS Provider 工具测试（Wave 4 迁移）

原 ObsControlHandler 拆分为三个独立工具：
- ``obs_send_text``
- ``obs_switch_scene``
- ``obs_set_source_visibility``

``OUTPUT_OBS_COMMAND`` 事件被删除（用户拍板：OBS 是工具，无事件），
因此旧 handler 的 ``_handle_obs_command_event`` 三参数签名测试不再适用。
本文件保留针对三个工具的单元测试。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.tools import ToolInvocation
from src.modules.tools.output.obs import obs_provider as obs_mod


def _make_obs_provider(monkeypatch: pytest.MonkeyPatch) -> "obs_mod.OBSProvider":
    """构造一个最小可用的 OBSProvider，绕过 obsws-python 依赖。"""
    monkeypatch.setattr(obs_mod, "obs", MagicMock())
    return obs_mod.OBSProvider(
        config={
            "type": "obs_control",
            "host": "127.0.0.1",
            "port": 4455,
            "password": None,
            "text_source_name": "text",
            "typewriter_enabled": False,
            "typewriter_speed": 0.1,
            "typewriter_delay": 0.5,
            "test_on_connect": False,
        },
        event_bus=MagicMock(spec=EventBus),
    )


@pytest.mark.asyncio
async def test_obs_send_text_tool_calls_send_text_to_obs(monkeypatch):
    """obs_send_text 工具 → 内部委托给 _send_text_to_obs"""
    provider = _make_obs_provider(monkeypatch)
    provider._send_text_to_obs = AsyncMock()

    result = await provider.invoke(
        ToolInvocation(tool_name="obs_send_text", arguments={"text": "hello"})
    )

    assert result.success is True
    provider._send_text_to_obs.assert_awaited_once_with("hello", None)


@pytest.mark.asyncio
async def test_obs_switch_scene_tool_calls_switch_scene(monkeypatch):
    """obs_switch_scene 工具 → 内部委托给 switch_scene"""
    provider = _make_obs_provider(monkeypatch)
    provider.switch_scene = AsyncMock()

    result = await provider.invoke(
        ToolInvocation(tool_name="obs_switch_scene", arguments={"scene_name": "main"})
    )

    assert result.success is True
    provider.switch_scene.assert_awaited_once_with("main")


@pytest.mark.asyncio
async def test_obs_set_source_visibility_tool(monkeypatch):
    """obs_set_source_visibility 工具 → 内部委托给 set_source_visibility"""
    provider = _make_obs_provider(monkeypatch)
    provider.set_source_visibility = AsyncMock()

    result = await provider.invoke(
        ToolInvocation(
            tool_name="obs_set_source_visibility",
            arguments={"source_name": "cam", "visible": True},
        )
    )

    assert result.success is True
    provider.set_source_visibility.assert_awaited_once_with("cam", True)


@pytest.mark.asyncio
async def test_obs_unknown_tool_returns_failure(monkeypatch):
    """未知工具名返回失败 ToolExecutionResult，不抛异常"""
    provider = _make_obs_provider(monkeypatch)

    result = await provider.invoke(
        ToolInvocation(tool_name="obs_unknown", arguments={})
    )

    assert result.success is False
    assert "未知" in result.error_message or "不属于" in result.error_message