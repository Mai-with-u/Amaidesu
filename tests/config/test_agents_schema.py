"""Agents 配置 Schema：enabled 名单、各 Agent 子配置（类型由包内权威配置提供，中央树只引用）、UI 元数据、往返"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from src.agents.minecraft.config import MinecraftConfig
from src.agents.streamer.config import StreamerConfig
from src.agents.text_adv.config import TextAdvConfig
from src.modules.agents.factory import SUPPORTED_AGENTS
from src.modules.config.agents_schemas import AgentsConfig, AgentsRootConfig, AgentType


def test_enabled_list_accepts_known_agents_only() -> None:
    assert isinstance(AgentsRootConfig().agents, AgentsConfig)
    cfg = AgentsConfig()
    assert isinstance(cfg.enabled, list) and "streamer" in cfg.enabled
    assert len(AgentsConfig(enabled=["streamer", "minecraft", "text_adv"]).enabled) == 3
    for bad in (["unknown_agent"], ["streamer", "game"]):  # [agents.game] 分类层已移除，"game" 不再是合法 Agent 名
        with pytest.raises(ValidationError):
            AgentsConfig(enabled=bad)
    with pytest.raises(ValidationError):
        AgentsConfig(game={"engine": "minecraft", "max_steps": 50})


def test_enabled_options_and_agent_type_stay_aligned_with_the_factory() -> None:
    extra = AgentsConfig.model_fields["enabled"].json_schema_extra or {}
    assert extra.get("x-options") == list(get_args(AgentType)) and "x-ui-type" not in extra  # 候选名单供下拉/多选渲染
    assert (
        set(get_args(AgentType)) == set(SUPPORTED_AGENTS) == {"streamer", "minecraft", "text_adv", "game_2048"}
    )  # 双源对齐，无分类层


def test_streamer_defaults_are_the_full_package_config() -> None:
    cfg = AgentsConfig()
    streamer = cfg.streamer
    assert isinstance(streamer, StreamerConfig)  # 默认就是完整配置，确保加载器自动补齐子树
    assert (streamer.persona.bot_name, streamer.persona.audience_salutation) == ("麦麦", "大家")
    assert streamer.context.enabled is True and not hasattr(
        streamer.context, "memory_recall_long_term"
    )  # 画像按本批发言人全量注入，无条数上限
    assert (streamer.background.light_tick_ms, streamer.background.compressor.concurrency) == (5_000, 1)
    assert (streamer.background.compressor.queue_max, streamer.batch.batch_window_ms) == (100, 3_000)
    assert streamer.force.force_message_types == ["super_chat", "guard", "gift"]
    assert (streamer.proactive.enabled, streamer.word_filter.enabled, streamer.command.prefix) == (True, False, "/")
    assert streamer.command.tool_mappings == []  # 工具映射表默认空：委派 mappings 不受影响
    assert streamer.thinking_stream.enabled is True
    assert not hasattr(streamer, "narrative")  # 叙事回放配置已随追加式历史退役


def test_minecraft_config_comes_from_its_package_and_has_no_step_limit() -> None:
    cfg = AgentsConfig(minecraft={"events_wait_ms": 4000})
    assert isinstance(cfg.minecraft, MinecraftConfig) and cfg.minecraft.events_wait_ms == 4000

    defaults = AgentsConfig().minecraft
    assert (defaults.context.max_tokens, defaults.context.window_ratio) == (200_000, 0.5)  # 保留上下文预算
    assert (
        "max_steps" not in defaults.model_dump()
        and "max_steps" not in MinecraftConfig.model_json_schema()["properties"]
    )
    with pytest.raises(ValidationError):  # 旧步数键须经配置迁移清理，不能成为可编辑但不生效的参数
        AgentsConfig(minecraft={"max_steps": 50})


def test_text_adv_config_comes_from_its_package() -> None:
    text_adv = AgentsConfig().text_adv
    assert isinstance(text_adv, TextAdvConfig)
    assert (text_adv.monitor_index, text_adv.region, text_adv.auto_button_xy) == (1, None, None)
    assert text_adv.keys == {"advance": "space", "skip": "ctrl", "menu": "backspace"}
    assert (text_adv.stability_sample_ms, text_adv.stability_consecutive, text_adv.stability_timeout_ms) == (
        150,
        2,
        5_000,
    )
    assert (text_adv.no_change_limit, text_adv.max_recent_screens, text_adv.game_window_title_keyword) == (20, 10, "")

    custom = AgentsConfig(text_adv={"auto_button_xy": [120, 240], "region": [0, 0, 1920, 1080]}).text_adv
    assert custom.auto_button_xy == (120, 240) and custom.region == [0, 0, 1920, 1080]


def test_model_dump_round_trip_keeps_custom_values() -> None:
    cfg = AgentsConfig(streamer={"persona": {"bot_name": "测试"}}, minecraft={"events_wait_ms": 4000})
    restored = AgentsConfig.model_validate(cfg.model_dump())
    assert restored.streamer.persona.bot_name == "测试" and restored.minecraft.events_wait_ms == 4000


class TestStreamerCommandToolMappings:
    """观众命令的工具映射表：遥控型游戏的命令直接变成一次固定工具调用。"""

    def test_tool_mappings_parse_as_list_of_tables(self):
        cfg = StreamerConfig.from_dict(
            {
                "command": {
                    "tool_mappings": [
                        {"command": "up", "tool": "game_2048_press", "arguments": {"key": "up"}},
                        {"command": "restart", "tool": "game_2048_press"},
                    ]
                }
            }
        )
        items = cfg.command.tool_mappings
        assert [(m.command, m.tool, m.arguments) for m in items] == [
            ("up", "game_2048_press", {"key": "up"}),
            ("restart", "game_2048_press", {}),
        ]

    def test_tool_mappings_default_empty_keeps_delegate_mappings_independent(self):
        cfg = StreamerConfig.from_dict({"command": {"mappings": {"come": "到主播身边来"}}})
        assert cfg.command.mappings == {"come": "到主播身边来"}
        assert cfg.command.tool_mappings == []
