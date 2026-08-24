"""Agents 配置 Schema 测试（v2.0.0：替代旧 [deciders] Schema 测试）

覆盖 src/modules/config/agents_schemas.py:
1. **AgentsRootConfig 顶层结构**：包含 agents (AgentsConfig)
2. **AgentsConfig 元数据**：enabled 列表 + 各 Agent 子配置
3. **StreamerAgentConfig**：替代旧 [deciders.amaidesu]
4. **GameAgentConfig**：游戏 Agent 占位
5. **json_schema_extra**：UI 元数据保留

段树详见 ``.omo/drafts/amaidesu-v2-config-tree.md``。
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from src.modules.config.agents_schemas import (
    AgentType,
    AgentsConfig,
    AgentsRootConfig,
    GameAgentConfig,
    StreamerAgentConfig,
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
        cfg = AgentsConfig(enabled=["streamer", "game"])
        assert len(cfg.enabled) == 2

    def test_unknown_agent_rejected(self):
        with pytest.raises(ValidationError):
            AgentsConfig(enabled=["unknown_agent"])


class TestAgentsConfigSubConfigs:
    def test_default_subconfigs_none(self):
        cfg = AgentsConfig()
        assert cfg.streamer is None
        assert cfg.game is None

    def test_streamer_subconfig(self):
        cfg = AgentsConfig(streamer={"planner_llm": "llm", "replyer_llm": "llm_fast"})
        assert cfg.streamer.planner_llm == "llm"
        assert cfg.streamer.replyer_llm == "llm_fast"

    def test_game_subconfig(self):
        cfg = AgentsConfig(game={"enabled": True, "engine": "minecraft", "command_llm": "llm"})
        assert cfg.game.enabled is True
        assert cfg.game.engine == "minecraft"
        assert cfg.game.command_llm == "llm"


class TestStreamerAgentConfig:
    def test_defaults(self):
        cfg = StreamerAgentConfig()
        assert cfg.planner_llm == "llm_fast"
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


class TestGameAgentConfig:
    def test_defaults(self):
        cfg = GameAgentConfig()
        assert cfg.engine == "minecraft"
        assert cfg.command_llm == "llm"

    def test_engine_field_override(self):
        cfg = GameAgentConfig(engine="stardew")
        assert cfg.engine == "stardew"


class TestJsonSchemaExtra:
    def test_enabled_has_ui_metadata(self):
        field_info = AgentsConfig.model_fields["enabled"]
        extra = field_info.json_schema_extra or {}
        assert extra.get("x-ui-type") == "multiselect"
        assert "streamer" in extra.get("x-options", [])


class TestAgentsConfigRoundTrip:
    def test_model_dump_round_trip(self):
        cfg = AgentsConfig(streamer={"planner_llm": "llm"})
        dumped = cfg.model_dump()
        cfg2 = AgentsConfig.model_validate(dumped)
        assert cfg2.streamer.planner_llm == "llm"


class TestAgentTypeLiteral:
    def test_agent_type_values(self):
        values = get_args(AgentType)
        assert "streamer" in values
        assert "game" in values
        assert "custom" in values