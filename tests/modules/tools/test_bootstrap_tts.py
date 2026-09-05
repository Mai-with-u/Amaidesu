"""bootstrap.py 回归测试

TTS 装配已迁出 ``bind_core_tools``（由 ``src.modules.tts.build_tts_infrastructure``
负责），本模块只覆盖非 TTS 工具包的白名单逻辑。
"""

from __future__ import annotations

import inspect

import pytest

from src.modules.tools import ToolRegistry
from src.modules.tools.bootstrap import _NON_TTS_PACKAGES, bind_core_tools


# ---------------------------------------------------------------------------
# bind_core_tools 不再处理 TTS
# ---------------------------------------------------------------------------


class TestBootstrapNoLongerHandlesTTS:
    """bind_core_tools 已退役 TTS 装配：不应注册任何 TTS 工具、不应接受 tts_config。"""

    def test_bind_core_tools_signature_has_no_tts_config_param(self):
        """``bind_core_tools`` 不再接受 ``tts_config`` 形参。"""
        sig = inspect.signature(bind_core_tools)
        assert "tts_config" not in sig.parameters, (
            "TTS 装配已迁出 bootstrap，bind_core_tools 不应再有 tts_config 形参"
        )

    def test_tts_provider_does_not_register_anything(self):
        """``bind_core_tools`` 不应注册任何 TTS 相关工具（TTS 由装配入口负责）。"""
        registry = ToolRegistry()
        bind_core_tools(
            registry,
            config={"enabled": ["subtitle", "vts"]},
        )
        tts_names = [n.name for n in registry.list_tools() if "tts" in n.name.lower()]
        assert tts_names == [], f"bind_core_tools 不应注册 TTS 工具，实际: {tts_names}"


# ---------------------------------------------------------------------------
# bind_core_tools 仍然只处理非 TTS 包（回归契约）
# ---------------------------------------------------------------------------


class TestNonTTSPackagesUnchanged:
    """非 TTS 包白名单逻辑保持不变（与历史契约一致）。"""

    def test_subtitle_in_enabled_list_registers_subtitle(self):
        registry = ToolRegistry()
        report = bind_core_tools(
            registry,
            config={"enabled": ["subtitle"]},
        )
        assert any(n.name == "push_subtitle" for n in registry.list_tools())
        assert report.get("subtitle", 0) > 0

    def test_vts_not_in_enabled_list_not_registered(self):
        """vts 未在 enabled 列表中：注册器不应有 vts_* 工具。"""
        registry = ToolRegistry()
        bind_core_tools(
            registry,
            config={"enabled": ["subtitle"]},
        )
        vts_tools = [n.name for n in registry.list_tools() if n.name.startswith("vts_")]
        assert vts_tools == [], f"vts 不应被装配，实际装配了 {vts_tools}"

    def test_missing_enabled_list_registers_nothing(self):
        """config 中无 enabled 字段 → 非 TTS 包一律不装配（防止隐式启用）。"""
        registry = ToolRegistry()
        bind_core_tools(registry, config={})
        for key, *_ in _NON_TTS_PACKAGES:
            for prefix in (key, f"{key}_"):
                assert not any(
                    n.name.startswith(prefix) for n in registry.list_tools()
                ), f"{key} 不应被装配"

    def test_non_registry_argument_raises_type_error(self):
        with pytest.raises(TypeError):
            bind_core_tools(registry="not a registry")  # type: ignore[arg-type]

    def test_returned_count_matches_registry_delta(self):
        """报告计数应与 registry 长度增量一致。"""
        registry = ToolRegistry()
        before = len(registry)
        report = bind_core_tools(
            registry,
            config={"enabled": ["subtitle"]},
        )
        delta = len(registry) - before
        assert sum(report.values()) == delta


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
