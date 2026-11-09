"""提示词不教已被 Mod 删除的能力，能力参数必须照签名填。

Mod 删掉了 wait 能力（站着等条件没有看头，也没有超时），
提示词与直播策略技能里都不能再教它；参数纪律那条
防止模型发明签名外的新参数名。
"""

from __future__ import annotations

from pathlib import Path

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
    assert "不要让身体站在原地等条件" in game
    assert "`sleep`" in game


def test_skill_policy_does_not_teach_the_removed_wait_ability() -> None:
    """直播策略技能里同样不留已删除的 wait 能力。"""
    text = _SKILL.read_text(encoding="utf-8")
    assert "`wait`" not in text
    assert "不要让身体站在原地等条件" in text


def test_parameters_must_come_from_the_signature() -> None:
    """能力参数照签名填：签名外没有的参数名不存在，必填参数漏了整笔被拒。"""
    game = _game_prompt()
    assert "签名里没列的参数名一律不存在" in game
    assert "必填参数漏了整笔会被拒收" in game
    assert "参数被拒收先对照签名查字段名" in game


def test_removed_wait_tool_is_not_taught_anywhere() -> None:
    """minecraft_wait 已移除：游戏侧提示词不得再教它（话轮结束即让出，无需工具）。"""
    game = _game_prompt()
    assert "minecraft_wait" not in game
