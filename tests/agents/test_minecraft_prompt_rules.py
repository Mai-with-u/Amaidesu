"""提示词不教已被 Mod 删除的能力。

Mod 删掉了 wait 能力（站着等条件没有看头，也没有超时），提示词与直播策略
技能里都不能再教它（退役能力名单守护）；工具调用边界与笔记口径的三处
一致性（提示词↔工具说明↔技能）保留在这里。
"""

from __future__ import annotations

from pathlib import Path

from src.agents.minecraft.tools import build_notebook_spec
from src.modules.prompts import get_prompt_manager, reset_prompt_manager

_SKILL = Path(__file__).parents[2] / "src" / "agents" / "minecraft" / "skills" / "streaming" / "streaming_policy.md"


def _game_prompt() -> str:
    reset_prompt_manager()
    try:
        return get_prompt_manager().render("amaidesu_minecraft_agent")
    finally:
        reset_prompt_manager()


def test_game_prompt_does_not_teach_the_removed_wait_ability() -> None:
    """提示词不再教 wait 能力，改指 timer 与 sleep。"""
    game = _game_prompt()
    assert "能力 `wait`" not in game
    assert "`sleep`" in game


def test_skill_policy_does_not_teach_the_removed_wait_ability() -> None:
    """直播策略技能里同样不留已删除的 wait 能力。"""
    text = _SKILL.read_text(encoding="utf-8")
    assert "`wait`" not in text


def test_removed_wait_tool_is_not_taught_anywhere() -> None:
    """minecraft_wait 已移除：游戏侧提示词不得再教它（话轮结束即让出，无需工具）。"""
    game = _game_prompt()
    assert "minecraft_wait" not in game


def test_game_prompt_covers_station_registration_and_three_way_memory_tool_boundaries() -> None:
    """提示词明确箱式加工站登记流程及 minecraft_rules / minecraft_notebook / minecraft_skill 三分边界。"""
    game = _game_prompt()
    assert 'remember(operation="station")' in game
    assert "minecraft_rules" in game
    assert "minecraft_notebook" in game
    assert "minecraft_skill" in game
    assert "scope=world" in game and "scope=global" in game


def test_notebook_boundary_excludes_changing_numbers_across_prompt_tool_and_skill() -> None:
    """笔记只写跨批次还成立的信息：提示词、笔记工具说明、直播策略技能三处口径保持一致。"""
    changing_numbers = "背包里现在有多少、身上穿的装备、身体此刻在哪、血量多少"
    game = _game_prompt()
    assert "只写跨批次之后还站得住的信息" in game and changing_numbers in game and "当场 `observe(self)`" in game

    description = build_notebook_spec().description
    assert "只写跨批次之后还站得住的信息" in description and changing_numbers in description

    assert changing_numbers in _SKILL.read_text(encoding="utf-8")
