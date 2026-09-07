"""Tools 配置 Schema 测试（v2.0.18 工具域开关重构后）

测试 src/modules/config/tools_schemas.py：
1. ToolsRootConfig 根结构（tools 子段）
2. ToolsConfig 聚合：enabled 列表 + perception/output 包 + 工具域开关（avatar/studio/vision/memory/mcp）
3. ToolProviderConfig 开关语义（enabled + config）
4. avatar/studio 动态子段（Dict[str, ProviderConfig]）
5. json_schema_extra UI 元数据
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.modules.config.tools_schemas import (
    AvatarProviderConfig,
    McpProviderConfig,
    MemoryProviderConfig,
    StudioProviderConfig,
    ToolProviderConfig,
    ToolPackType,
    ToolsConfig,
    ToolsRootConfig,
    VisionProviderConfig,
)


class TestToolsRootConfig:
    def test_default_construction(self):
        cfg = ToolsRootConfig()
        assert isinstance(cfg.tools, ToolsConfig)

    def test_tools_section_present(self):
        cfg = ToolsRootConfig()
        assert cfg.tools is not None


class TestToolsConfigEnabled:
    def test_default_enabled_is_list(self):
        cfg = ToolsConfig()
        assert isinstance(cfg.enabled, list)

    def test_default_enabled_contains_perception_output(self):
        cfg = ToolsConfig()
        assert "perception" in cfg.enabled
        assert "output" in cfg.enabled

    def test_accepts_known_packs(self):
        cfg = ToolsConfig(enabled=["perception", "output"])
        assert len(cfg.enabled) == 2

    def test_unknown_pack_rejected(self):
        with pytest.raises(ValidationError):
            ToolsConfig(enabled=["unknown_pack"])


class TestToolsConfigPackAndDomains:
    def test_packs_default_none(self):
        cfg = ToolsConfig()
        for pack in ("perception", "output"):
            assert getattr(cfg, pack) is None, f"{pack} 应默认 None"

    def test_domains_default_empty_or_none(self):
        cfg = ToolsConfig()
        assert cfg.avatar == {} or cfg.avatar is None
        assert cfg.studio == {} or cfg.studio is None
        assert cfg.vision is None
        assert cfg.memory is None
        assert cfg.mcp is None


class TestToolProviderConfig:
    def test_default_enabled_true(self):
        cfg = ToolProviderConfig()
        assert cfg.enabled is True
        assert cfg.config == {}

    def test_custom_enabled_and_config(self):
        cfg = ToolProviderConfig(enabled=False, config={"key": "value"})
        assert cfg.enabled is False
        assert cfg.config == {"key": "value"}


class TestAvatarStudioDomains:
    def test_avatar_accepts_dynamic_subdomains(self):
        cfg = ToolsConfig(avatar={"vts": {"enabled": True, "config": {}}})
        assert cfg.avatar["vts"].enabled is True

    def test_studio_accepts_dynamic_subdomains(self):
        cfg = ToolsConfig(studio={"obs": {"enabled": True, "config": {}}})
        assert cfg.studio["obs"].enabled is True

    def test_domain_extra_allowed(self):
        cfg = AvatarProviderConfig(enabled=True, extra_field="x")
        assert cfg.extra_field == "x"


class TestSpecializedDomains:
    def test_vision(self):
        cfg = VisionProviderConfig(enabled=True, config={"default_max_width": 1280})
        assert cfg.enabled is True

    def test_memory(self):
        cfg = MemoryProviderConfig(enabled=True)
        assert cfg.config == {}

    def test_mcp(self):
        cfg = McpProviderConfig(enabled=True, config={"servers": {}})
        assert cfg.config == {"servers": {}}

    def test_studio_specialized(self):
        cfg = StudioProviderConfig(enabled=True)
        assert cfg.enabled is True


class TestJsonSchemaExtra:
    def test_enabled_has_ui_metadata(self):
        field_info = ToolsConfig.model_fields["enabled"]
        extra = field_info.json_schema_extra or {}
        assert extra.get("x-ui-type") == "multiselect"
        assert "perception" in extra.get("x-options", [])
        assert "output" in extra.get("x-options", [])
