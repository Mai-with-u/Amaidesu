"""bind_core_tools 白名单装配：不再处理 TTS，非 TTS 工具包按域开关装配，无域配置不隐式启用"""

from __future__ import annotations

import inspect

import pytest

from src.modules.tools import ToolRegistry
from src.modules.tools.bootstrap import bind_core_tools


def test_bind_core_tools_no_longer_handles_tts_or_unconfigured_domains() -> None:
    # TTS 装配已迁出 bootstrap：既无 tts_config 形参，也不注册任何 TTS 工具
    assert "tts_config" not in inspect.signature(bind_core_tools).parameters

    for config in ({}, {"enabled": ["vts"]}):  # 无域配置：不隐式启用任何包
        registry = ToolRegistry()
        before = len(registry)
        report = bind_core_tools(registry, config=config)
        assert registry.list_tools() == [] and sum(report.values()) == len(registry) - before


def test_avatar_vts_is_registered_degraded_only_when_enabled() -> None:
    registry = ToolRegistry()
    report = bind_core_tools(registry, config={}, avatar_platform={"enabled": ["vts"], "vts": {"vts_port": 8001}})
    # 装配期未连接 → 降级登记：Provider 在册（可见可重连）但 0 工具，连接成功后再补注册
    assert [t for t in registry.list_tools() if t.full_name.startswith("vts_")] == []
    cards = [c for c in registry.list_providers() if c["name"] == "vts"]
    assert len(cards) == 1 and cards[0]["tool_count"] == 0 and report.get("vts", 0) == 0

    registry = ToolRegistry()
    bind_core_tools(registry, config={}, avatar_platform={"enabled": [], "vts": {"vts_port": 8001}})
    assert not [c for c in registry.list_providers() if c["name"] == "vts"]  # 不在启用名单不装配


def test_studio_obs_enabled_registers_obs_tools() -> None:
    registry = ToolRegistry()
    report = bind_core_tools(registry, config={"studio": {"obs": {"enabled": True, "config": {}}}})
    # 装配期未连接 → 降级登记：Provider 在册（可见可重连）但 0 工具，连接成功后再补注册
    assert [t for t in registry.list_tools() if t.full_name.startswith("obs_")] == []
    cards = [c for c in registry.list_providers() if c["name"] == "obs"]
    assert len(cards) == 1 and cards[0]["tool_count"] == 0 and report.get("obs", 0) == 0


def test_non_registry_argument_raises_type_error() -> None:
    with pytest.raises(TypeError):
        bind_core_tools(registry="not a registry")  # type: ignore[arg-type]
