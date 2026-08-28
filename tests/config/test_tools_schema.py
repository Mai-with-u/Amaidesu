"""Tools 配置 Schema 测试（v2.0.0：替代旧 [collectors]/[handlers] Schema 测试）

覆盖 src/modules/config/tools_schemas.py:
1. **ToolsRootConfig 顶层结构**：包含 tools (ToolsConfig) 子段
2. **ToolsConfig 元数据**：enabled 列表 + 各工具包 (perception/understanding/output/...)
3. **ToolPackMeta**：每个工具包通用配置
4. **5 个已知工具包均为 Optional**
5. **json_schema_extra**：UI 元数据保留

段树详见 ``.omo/drafts/amaidesu-v2-config-tree.md``。
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from src.modules.config.tools_schemas import (
    LookAtScreenToolConfig,
    ToolPackMeta,
    ToolPackType,
    ToolsConfig,
    ToolsRootConfig,
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

    def test_accepts_all_known_packs(self):
        cfg = ToolsConfig(enabled=["perception", "understanding", "output", "content_engine", "external"])
        assert len(cfg.enabled) == 5

    def test_unknown_pack_rejected(self):
        with pytest.raises(ValidationError):
            ToolsConfig(enabled=["unknown_pack"])


class TestToolsConfigPacks:
    def test_all_packs_default_none(self):
        cfg = ToolsConfig()
        for pack in ("perception", "understanding", "output", "content_engine", "external"):
            assert getattr(cfg, pack) is None, f"{pack} 应默认 None"

    def test_perception_pack_meta(self):
        cfg = ToolsConfig(perception={"enabled": True, "provider": "builtin"})
        assert cfg.perception.enabled is True
        assert cfg.perception.provider == "builtin"

    def test_output_pack_meta(self):
        cfg = ToolsConfig(output={"enabled": True, "provider": "builtin"})
        assert cfg.output.enabled is True

    def test_provider_literal_validation(self):
        ToolsConfig(perception={"provider": "builtin"})
        ToolsConfig(perception={"provider": "game"})
        ToolsConfig(perception={"provider": "mcp"})
        with pytest.raises(ValidationError):
            ToolsConfig(perception={"provider": "invalid"})


class TestJsonSchemaExtra:
    def test_enabled_has_ui_metadata(self):
        field_info = ToolsConfig.model_fields["enabled"]
        extra = field_info.json_schema_extra or {}
        assert extra.get("x-ui-type") == "multiselect"
        assert "perception" in extra.get("x-options", [])

    def test_provider_has_ui_metadata(self):
        field_info = ToolPackMeta.model_fields["provider"]
        extra = field_info.json_schema_extra or {}
        assert extra.get("x-ui-type") == "select"
        assert "builtin" in extra.get("x-options", [])


class TestToolsConfigRoundTrip:
    def test_model_dump_round_trip(self):
        cfg = ToolsConfig()
        dumped = cfg.model_dump()
        cfg2 = ToolsConfig.model_validate(dumped)
        assert cfg2.enabled == cfg.enabled


class TestToolPackTypeLiteral:
    def test_tool_pack_type_values(self):
        values = get_args(ToolPackType)
        assert "perception" in values
        assert "output" in values


class TestLookAtScreenToolConfig:
    """[tools.look_at_screen] 屏幕快照工具段（v2.0.7 W7 前置）"""

    def test_default_construction(self):
        cfg = LookAtScreenToolConfig()
        assert cfg.enabled is True
        assert cfg.default_max_width == 1280

    def test_tools_config_default_not_none(self):
        cfg = ToolsConfig()
        assert isinstance(cfg.look_at_screen, LookAtScreenToolConfig)

    def test_extra_forbid(self):
        with pytest.raises(ValidationError):
            LookAtScreenToolConfig(unknown_field=1)

    def test_round_trip(self):
        cfg = ToolsConfig()
        dumped = cfg.model_dump()
        cfg2 = ToolsConfig.model_validate(dumped)
        assert cfg2.look_at_screen == cfg.look_at_screen

    def test_enabled_true_accepted(self):
        cfg = LookAtScreenToolConfig(enabled=True, default_max_width=0)
        assert cfg.enabled is True
        assert cfg.default_max_width == 0
