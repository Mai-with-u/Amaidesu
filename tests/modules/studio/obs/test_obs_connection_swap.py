"""OBSProvider 连接态→工具表换血（缺陷修复：断连后工具不再暴露可调）。

覆盖：
- 装配期未连接降级登记 0 工具，连接成功（setup/手动 connect）补注册全量
- disconnect / cleanup 摘除断连工具
- invoke 捕获连接类异常 → 首次失败即摘除；业务性失败不误摘
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.studio.obs import obs_provider as obs_mod
from src.modules.tools.models import ToolExecutionResult, ToolInvocation
from src.modules.tools.registry import ToolRegistry

_OBS_TOOLS = {"send_text", "switch_scene", "set_source_visibility", "send_test"}


class WebSocketConnectionClosedException(Exception):
    """仿 websocket 库的连接关闭异常（_is_connection_error 按类名识别，不依赖真实依赖）。"""


def _config() -> Dict[str, Any]:
    return {
        "type": "obs_control",
        "host": "127.0.0.1",
        "port": 4455,
        "password": "",
        "text_source_name": "text",
        "test_on_connect": False,
    }


def _make_obs_provider(monkeypatch: pytest.MonkeyPatch) -> obs_mod.OBSProvider:
    monkeypatch.setattr(obs_mod, "obs", MagicMock())
    return obs_mod.OBSProvider(config=_config(), event_bus=None)


async def _call(
    provider: obs_mod.OBSProvider, tool: str, arguments: Dict[str, Any] | None = None
) -> ToolExecutionResult:
    return await provider.invoke(ToolInvocation(tool_name=tool, arguments=arguments or {}, source="test"))


def _registry_names(registry: ToolRegistry) -> set[str]:
    return {s.name for s in registry.list_tools(provider="obs")}


def test_assembly_registers_zero_tools_until_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    """装配期未连接：降级登记 0 工具（Provider 在册），连接成功补注册全量。"""
    registry = ToolRegistry()
    provider = obs_mod.register_obs_tools(registry, _config())
    assert _registry_names(registry) == set()

    provider.mark_connected()
    assert _registry_names(registry) == _OBS_TOOLS  # 连接建立：4 工具补注册


async def test_disconnect_and_cleanup_drop_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """断连（手动 disconnect / cleanup）后工具表摘除，list_tools 为空。"""
    provider = _make_obs_provider(monkeypatch)
    provider.mark_connected()
    assert len(provider.list_tools()) == 4

    provider.obs_connection = MagicMock(disconnect=MagicMock())
    assert await provider.disconnect() is True
    assert provider.is_connected is False
    assert provider.list_tools() == []  # 断连后工具不再暴露可调（缺陷修复点）

    provider.mark_connected()
    provider.obs_connection = MagicMock(disconnect=MagicMock())
    provider._has_started = True
    await provider.cleanup()
    assert provider.list_tools() == []


async def test_connect_success_after_failure_reRegisters_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """手动重连成功补注册：握手失败摘除后，连接成功再补回全量。"""
    registry = ToolRegistry()
    provider = obs_mod.register_obs_tools(registry, _config())
    provider.mark_connected()

    # 首次连接类失败（握手失败视为连接类，摘除既有登记）
    req_client = MagicMock(side_effect=ConnectionResetError("连接被重置"))
    monkeypatch.setattr(obs_mod, "obs", MagicMock(ReqClient=req_client))
    assert await provider.connect() is False
    assert _registry_names(registry) == set()

    # 手动重连成功：4 工具补回
    monkeypatch.setattr(obs_mod, "obs", MagicMock(ReqClient=MagicMock(return_value=MagicMock())))
    assert await provider.connect() is True
    assert _registry_names(registry) == _OBS_TOOLS


async def test_invoke_connection_error_drops_tools_business_error_keeps(monkeypatch: pytest.MonkeyPatch) -> None:
    """invoke 捕获连接类异常 → 首次失败即摘除；业务性失败不误摘。"""
    registry = ToolRegistry()
    provider = obs_mod.register_obs_tools(registry, _config())
    provider.mark_connected()

    # 业务性失败（场景不存在，业务方法内部已兜成失败结果）：不摘
    await _call(provider, "obs_switch_scene")
    assert _registry_names(registry) == _OBS_TOOLS

    # 连接类异常（业务方法上抛连接关闭类异常）：首次失败即摘
    provider.switch_scene = AsyncMock(side_effect=WebSocketConnectionClosedException("connection is closed."))  # type: ignore[method-assign]
    result = await _call(provider, "obs_switch_scene", {"scene_name": "x"})
    assert result.success is False
    assert _registry_names(registry) == set()
    assert provider.list_tools() == []
