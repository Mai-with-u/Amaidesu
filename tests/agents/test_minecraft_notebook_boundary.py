"""笔记只写跨批次还成立的信息：背包、装备、位置、血量这类当下会变的数字不写进笔记。

守护方式是把提示词与工具说明里的关键句子断言出来——被改掉时测试直接红。
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


def test_game_prompt_keeps_only_the_facts_that_survive_a_new_batch() -> None:
    """游戏 Agent 提示词：笔记只写跨批次还成立的信息，当下会变的数字不写。"""
    game = _game_prompt()
    assert "只写跨批次之后还站得住的信息" in game
    assert "背包里现在有多少、身上穿的装备、身体此刻在哪、血量多少" in game
    assert "当场 `observe(self)`" in game


def test_notebook_tool_description_excludes_the_changing_numbers() -> None:
    """笔记工具说明同样划界：模型写笔记时看到的就是这条口径。"""
    description = build_notebook_spec().description
    assert "只写跨批次之后还站得住的信息" in description
    assert "背包里现在有多少、身上穿的装备、身体此刻在哪、血量多少" in description


def test_skill_policy_keeps_the_changing_numbers_out_of_the_notebook() -> None:
    """直播策略技能里的笔记口径也要划界（技能正文同样会进模型的上下文）。"""
    text = _SKILL.read_text(encoding="utf-8")
    assert "背包里现在有多少、身上穿的装备、身体此刻在哪、血量多少" in text
