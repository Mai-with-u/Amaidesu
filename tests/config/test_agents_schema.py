"""Agents 配置 Schema 测试

覆盖 src/modules/config/agents_schemas.py:
1. **AgentsRootConfig 顶层结构**：包含 agents (AgentsConfig)
2. **AgentsConfig 元数据**：enabled 列表 + 各 Agent 自包含子配置
3. **各 Agent Config**：minecraft / text_adv 字段与校验（streamer 侧已迁
   至包内权威 src/agents/streamer/config.py；本文件保留类型断言）
4. **json_schema_extra**：UI 元数据保留
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from src.agents.minecraft.config import MinecraftConfig
from src.agents.text_adv.config import TextAdvConfig
from src.modules.config.agents_schemas import (
    AgentType,
    AgentsConfig,
    AgentsRootConfig,
)


class TestAgentsRootConfig:
    def test_default_construction(self):
        cfg = AgentsRootConfig()
        assert isinstance(cfg.agents, AgentsConfig)

    def test_agents_section_present(self):
        cfg = AgentsRootConfig()
        assert cfg.agents is not None


class TestAgentsConfigEnabled:
    def test_default_enabled_is_list(self):
        cfg = AgentsConfig()
        assert isinstance(cfg.enabled, list)

    def test_default_enabled_contains_streamer(self):
        cfg = AgentsConfig()
        assert "streamer" in cfg.enabled

    def test_accepts_known_agents(self):
        cfg = AgentsConfig(enabled=["streamer", "minecraft", "text_adv"])
        assert len(cfg.enabled) == 3

    def test_unknown_agent_rejected(self):
        with pytest.raises(ValidationError):
            AgentsConfig(enabled=["unknown_agent"])

    def test_legacy_game_name_rejected(self):
        """[agents.game] 分类层已移除，"game" 不再是合法 Agent 名。"""
        with pytest.raises(ValidationError):
            AgentsConfig(enabled=["streamer", "game"])


class TestAgentsConfigSubConfigs:
    def test_streamer_default_is_full_config(self):
        """streamer 默认是完整 StreamerConfig 实例（确保加载器自动补齐子树）。"""
        cfg = AgentsConfig()
        assert cfg.streamer is not None
        assert cfg.streamer.persona.bot_name == "麦麦"
        assert cfg.streamer.persona.audience_salutation == "大家"

    def test_minecraft_subconfig_self_contained(self):
        """minecraft 是顶级子配置，类型由包内权威 MinecraftConfig 提供。"""
        cfg = AgentsConfig(minecraft={"max_steps": 80})
        assert isinstance(cfg.minecraft, MinecraftConfig)
        assert cfg.minecraft.max_steps == 80

    def test_text_adv_subconfig_self_contained(self):
        cfg = AgentsConfig(text_adv={"decision_strategy": "llm"})
        assert isinstance(cfg.text_adv, TextAdvConfig)
        assert cfg.text_adv.decision_strategy == "llm"

    def test_game_section_rejected(self):
        with pytest.raises(ValidationError):
            AgentsConfig(game={"engine": "minecraft", "max_steps": 50})


class TestStreamerConfigInAgentsTree:
    """streamer 字段类型由包内权威 StreamerConfig 提供；中央树仅引用。"""

    def test_streamer_uses_package_authoritative_schema(self):
        from src.agents.streamer.config import StreamerConfig

        cfg = AgentsConfig()
        assert isinstance(cfg.streamer, StreamerConfig)

    def test_streamer_nested_subkeys_present(self):
        cfg = AgentsConfig()
        s = cfg.streamer
        assert s.persona.audience_salutation == "大家"
        assert s.context.enabled is True
        assert s.context.memory_recall_long_term == 3
        assert s.background.light_tick_ms == 5_000
        assert s.background.compressor.concurrency == 1
        assert s.background.compressor.queue_max == 100
        assert s.batch.batch_window_ms == 3_000
        assert s.force.force_data_types == ["super_chat", "guard", "gift"]
        assert s.proactive.enabled is True
        assert s.word_filter.enabled is False
        assert s.command.prefix == "/"
        assert s.thinking_stream.enabled is True


class TestMinecraftPackageConfig:
    """minecraft 字段类型由包内权威 MinecraftConfig 提供；中央树仅引用。"""

    def test_uses_package_authoritative_schema(self):
        cfg = AgentsConfig()
        assert isinstance(cfg.minecraft, MinecraftConfig)

    def test_defaults(self):
        cfg = AgentsConfig()
        assert cfg.minecraft.max_steps == 50

    def test_field_overrides(self):
        cfg = AgentsConfig(minecraft={"max_steps": 120})
        assert cfg.minecraft.max_steps == 120

    def test_max_steps_below_minimum_rejected(self):
        with pytest.raises(ValidationError):
            AgentsConfig(minecraft={"max_steps": 0})


class TestTextAdvPackageConfig:
    """text_adv 字段类型由包内权威 TextAdvConfig 提供；中央树仅引用。"""

    def test_uses_package_authoritative_schema(self):
        cfg = AgentsConfig()
        assert isinstance(cfg.text_adv, TextAdvConfig)

    def test_defaults(self):
        cfg = AgentsConfig()
        assert cfg.text_adv.engine_kind == "text_adv"
        assert cfg.text_adv.decision_strategy == "first_option"
        assert cfg.text_adv.enable_event_emission is True

    def test_field_overrides(self):
        cfg = AgentsConfig(text_adv={"enable_event_emission": False})
        assert cfg.text_adv.enable_event_emission is False


class TestJsonSchemaExtra:
    def test_enabled_has_ui_metadata(self):
        """enabled 的 UI 元数据：候选名单（x-options）供下拉/多选渲染。"""
        field_info = AgentsConfig.model_fields["enabled"]
        extra = field_info.json_schema_extra or {}
        assert "x-ui-type" not in extra  # 非规范 ui-type 标记已清（T28）
        assert set(extra.get("x-options", [])) == {"streamer", "minecraft", "text_adv"}


class TestAgentsConfigRoundTrip:
    def test_model_dump_round_trip(self):
        cfg = AgentsConfig(streamer={"persona": {"bot_name": "测试"}})
        dumped = cfg.model_dump()
        cfg2 = AgentsConfig.model_validate(dumped)
        assert cfg2.streamer.persona.bot_name == "测试"

    def test_game_agent_round_trip(self):
        cfg = AgentsConfig(minecraft={"max_steps": 66})
        dumped = cfg.model_dump()
        cfg2 = AgentsConfig.model_validate(dumped)
        assert cfg2.minecraft.max_steps == 66


class TestAgentTypeLiteral:
    def test_agent_type_values_flat(self):
        """无分类层：AgentType 即顶级注册名全集，game/custom 已移除。"""
        values = get_args(AgentType)
        assert set(values) == {"streamer", "minecraft", "text_adv"}
