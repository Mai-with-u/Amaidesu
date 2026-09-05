"""bootstrap.py 回归测试

TTS 装配已迁出 ``bind_core_tools``（由 ``src.modules.tts.build_tts_infrastructure``
负责），本模块只覆盖非 TTS 工具包的白名单逻辑。

字幕（subtitle）工具形态已退役——字幕由 ``src.modules.subtitle.
build_subtitle_infrastructure`` 自治装配，不经 ``ToolRegistry``。bootstrap
对 ``enabled`` 列表中残留的 ``"subtitle"`` 条目保持静默忽略（白名单已
无对应映射项），确保旧配置文件冷启动不报错。
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
            config={"enabled": ["vts"]},
        )
        tts_names = [n.name for n in registry.list_tools() if "tts" in n.name.lower()]
        assert tts_names == [], f"bind_core_tools 不应注册 TTS 工具，实际: {tts_names}"


# ---------------------------------------------------------------------------
# bind_core_tools 仍然只处理非 TTS 包（回归契约）
# ---------------------------------------------------------------------------


class TestNonTTSPackagesUnchanged:
    """非 TTS 包白名单逻辑保持不变（与历史契约一致）。"""

    def test_subtitle_in_enabled_list_no_longer_registers_tool(self):
        """字幕工具形态已退役：``enabled`` 列表中包含 ``"subtitle"`` 不再注册工具。

        字幕由 ``src.modules.subtitle.build_subtitle_infrastructure`` 自治
        装配并注入 StreamerAgent；bootstrap 对残留 ``"subtitle"`` 条目保持
        静默忽略（白名单已无对应映射项），冷启动不报错。
        """
        registry = ToolRegistry()
        report = bind_core_tools(
            registry,
            config={"enabled": ["subtitle"]},
        )
        assert registry.list_tools() == [], "字幕工具形态已退役，bootstrap 不应再注册任何工具"
        assert "subtitle" not in report, "subtitle 不应出现在报告中（白名单已无映射项）"

    def test_vts_in_enabled_list_registers_vts_tools(self):
        """vts 在 enabled 列表中：应注册 vts_* 工具。"""
        registry = ToolRegistry()
        report = bind_core_tools(
            registry,
            config={"enabled": ["vts"]},
        )
        vts_tools = [n.name for n in registry.list_tools() if n.name.startswith("vts_")]
        assert vts_tools != [], "vts 在 enabled 中应被注册"
        assert report.get("vts", 0) > 0

    def test_subtitle_in_enabled_does_not_block_other_packages(self):
        """``enabled`` 同时包含 ``"subtitle"`` 与 ``"vts"`` 时：subtitle 静默忽略，vts 正常注册。"""
        registry = ToolRegistry()
        report = bind_core_tools(
            registry,
            config={"enabled": ["subtitle", "vts"]},
        )
        vts_tools = [n.name for n in registry.list_tools() if n.name.startswith("vts_")]
        assert vts_tools != [], "vts 应被注册"
        assert "subtitle" not in report, "subtitle 不应在报告中出现（白名单已无映射项）"

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
            config={"enabled": ["vts"]},
        )
        delta = len(registry) - before
        assert sum(report.values()) == delta


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
