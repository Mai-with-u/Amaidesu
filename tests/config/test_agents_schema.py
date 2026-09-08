"""Agents 配置 Schema 测试

覆盖 src/modules/config/agents_schemas.py:
1. **AgentsRootConfig 顶层结构**：包含 agents (AgentsConfig)
2. **AgentsConfig 元数据**：enabled 列表 + 各 Agent 自包含子配置
3. **各 Agent Config**：streamer / minecraft / text_adv 字段与校验
4. **json_schema_extra**：UI 元数据保留
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from src.modules.config.agents_schemas import (
    AgentType,
    AgentsConfig,
    AgentsRootConfig,
    MinecraftAgentConfig,
    StreamerAgentConfig,
    TextAdvAgentConfig,
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
    def test_default_subconfigs_none(self):
        cfg = AgentsConfig()
        assert cfg.streamer is None
        assert cfg.minecraft is None
        assert cfg.text_adv is None

    def test_streamer_subconfig(self):
        cfg = AgentsConfig(streamer={"planner_llm": "llm", "replyer_llm": "llm_fast"})
        assert cfg.streamer.planner_llm == "llm"
        assert cfg.streamer.replyer_llm == "llm_fast"

    def test_minecraft_subconfig_self_contained(self):
        """minecraft 是顶级子配置，自包含全部字段（无 [agents.game] 公共段）。"""
        cfg = AgentsConfig(minecraft={"command_llm": "llm", "max_steps": 80})
        assert cfg.minecraft.command_llm == "llm"
        assert cfg.minecraft.max_steps == 80

    def test_text_adv_subconfig_self_contained(self):
        cfg = AgentsConfig(text_adv={"command_llm": "llm", "decision_strategy": "llm"})
        assert cfg.text_adv.command_llm == "llm"
        assert cfg.text_adv.decision_strategy == "llm"

    def test_game_section_rejected(self):
        with pytest.raises(ValidationError):
            AgentsConfig(game={"engine": "minecraft", "command_llm": "llm"})


class TestStreamerAgentConfig:
    def test_defaults(self):
        cfg = StreamerAgentConfig()
        assert cfg.planner_llm == "llm"
        assert cfg.planner_max_steps == 8
        assert cfg.replyer_llm == "llm"
        assert cfg.room_state_enabled is True
        assert cfg.batch_window_ms == 3000
        assert cfg.batch_max_size == 20

    def test_planner_replier_llm_overrides(self):
        cfg = StreamerAgentConfig(
            planner_llm="llm",
            replyer_llm="llm_fast",
        )
        assert cfg.planner_llm == "llm"
        assert cfg.replyer_llm == "llm_fast"

    def test_room_state_cold_timeout_must_be_non_negative(self):
        with pytest.raises(ValidationError):
            StreamerAgentConfig(room_state_cold_timeout_ms=-1)


class TestMinecraftAgentConfig:
    def test_defaults(self):
        cfg = MinecraftAgentConfig()
        assert cfg.command_llm == "llm"
        assert cfg.max_steps == 50

    def test_field_overrides(self):
        cfg = MinecraftAgentConfig(command_llm="llm_fast", max_steps=120)
        assert cfg.command_llm == "llm_fast"
        assert cfg.max_steps == 120

    def test_max_steps_below_minimum_rejected(self):
        with pytest.raises(ValidationError):
            MinecraftAgentConfig(max_steps=0)


class TestTextAdvAgentConfig:
    def test_defaults(self):
        cfg = TextAdvAgentConfig()
        assert cfg.command_llm == "llm"
        assert cfg.engine_kind == "text_adv"
        assert cfg.decision_strategy == "first_option"
        assert cfg.enable_event_emission is True

    def test_field_overrides(self):
        cfg = TextAdvAgentConfig(command_llm="llm_fast", enable_event_emission=False)
        assert cfg.command_llm == "llm_fast"
        assert cfg.enable_event_emission is False


class TestJsonSchemaExtra:
    def test_enabled_has_ui_metadata(self):
        field_info = AgentsConfig.model_fields["enabled"]
        extra = field_info.json_schema_extra or {}
        assert extra.get("x-ui-type") == "multiselect"
        assert set(extra.get("x-options", [])) == {"streamer", "minecraft", "text_adv"}


class TestAgentsConfigRoundTrip:
    def test_model_dump_round_trip(self):
        cfg = AgentsConfig(streamer={"planner_llm": "llm"})
        dumped = cfg.model_dump()
        cfg2 = AgentsConfig.model_validate(dumped)
        assert cfg2.streamer.planner_llm == "llm"

    def test_game_agent_round_trip(self):
        cfg = AgentsConfig(minecraft={"command_llm": "llm", "max_steps": 66})
        dumped = cfg.model_dump()
        cfg2 = AgentsConfig.model_validate(dumped)
        assert cfg2.minecraft.max_steps == 66


class TestAgentTypeLiteral:
    def test_agent_type_values_flat(self):
        """无分类层：AgentType 即顶级注册名全集，game/custom 已移除。"""
        values = get_args(AgentType)
        assert set(values) == {"streamer", "minecraft", "text_adv"}
