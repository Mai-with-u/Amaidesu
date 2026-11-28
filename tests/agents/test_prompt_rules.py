"""出过事故的提示词规则集中清单的守护测试。

清单本体在 ``tests/agents/prompt_rules.py``，每行一条规则。这里按清单参数化，
逐条核对关键片段在对应提示词里的在场与缺席。失败信息必须能让人直接动手：
改了措辞就更新清单里那一行；有意撤掉规则就删掉那一行，并在提交说明里写明
这条事故经验为什么不再需要。
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from src.modules.prompts import PromptManager, get_prompt_manager, reset_prompt_manager
from tests.agents.prompt_rules import PROMPT_RULES, PromptRule

# 带占位变量的模板按生产测试同样的哑变量渲染（PromptManager 严格渲染，缺/多变量都报错）。
# 清单行没有单独指定 render_vars 时按模板名取这里的默认值。
DEFAULT_RENDER_VARS: dict[str, dict[str, str]] = {
    "amaidesu_planner_react": {
        "bot_name": "麦麦",
        "personality": "活泼",
        "behavior_style": "积极互动",
        "boundaries_rule": "",
        "inside_jokes": "",
        "game_rules": "",
        "social_fact_step": "",
    },
    "amaidesu_replyer_system": {
        "bot_name": "麦麦",
        "personality": "活泼",
        "style_section": "",
        "voice_examples_section": "",
        "boundaries_rule": "",
        "persona_profile_section": "",
        "speech_style_rule": "",
        "game_rule_line": "",
    },
    "amaidesu_replyer": {
        "plan": "",
        "turn_context": "",
        "game_chat": "",
        "game_people_section": "",
        "rundown": "",
        "dialogue_prompt": "",
        "recent_speech_section": "",
        "remembered_section": "",
        "state_facts": "",
        "bot_name": "麦麦",
        "length_rule": "",
        "game_heading": "",
        "game_turn_rules": "",
        "pace_rule": "",
    },
    "amaidesu_mouth": {
        "bot_name": "麦麦",
        "personality": "活泼",
        "style_constraints": "口语化",
        "max_chars": "40",
        "emotions": "happy",
        "boundaries_rule": "",
    },
}


def _prompt_text(rule: PromptRule) -> str:
    """按 prompt 字段取提示词全文：template: 经 PromptManager 渲染，const: 导入常量。"""
    kind, _, target = rule.prompt.partition(":")
    if kind == "template":
        reset_prompt_manager()
        try:
            manager: PromptManager = get_prompt_manager()
            kwargs: dict[str, Any] = dict(rule.render_vars) or dict(DEFAULT_RENDER_VARS.get(target, {}))
            return str(manager.render(target, **kwargs))
        finally:
            reset_prompt_manager()
    if kind == "const":
        module_path, _, attr = target.rpartition(".")
        if not module_path:
            raise ValueError(f"const 写法应是 '<模块点分路径>.<常量名>'，得到: {rule.prompt}")
        module = importlib.import_module(module_path)
        return str(getattr(module, attr))
    raise ValueError(f"prompt 字段应以 'template:' 或 'const:' 开头，得到: {rule.prompt}")


def _how_to_fix(rule_id: str) -> str:
    return (
        f"接下来怎么办：改了提示词措辞就更新 tests/agents/prompt_rules.py 里规则 '{rule_id}' 的"
        f" required/forbidden 片段；有意撤掉这条规则就删掉那一行，并在提交说明里写明"
        f"为什么这条事故经验不再需要。"
    )


@pytest.mark.parametrize("rule", PROMPT_RULES, ids=[rule.rule_id for rule in PROMPT_RULES])
def test_prompt_rule_fragments_present(rule: PromptRule) -> None:
    """清单里每条事故规则的关键片段都必须在对应提示词里（required 在场、forbidden 缺席）。"""
    text = _prompt_text(rule)
    missing = [fragment for fragment in rule.required if fragment not in text]
    present_forbidden = [fragment for fragment in rule.forbidden if fragment in text]
    problems: list[str] = []
    if missing:
        problems.append(f"缺少片段: {missing}")
    if present_forbidden:
        problems.append(f"不得出现的片段仍存在: {present_forbidden}")
    assert not problems, (
        f"事故规则 '{rule.rule_id}'（{rule.summary}）在 {rule.prompt} 中核对失败："
        f"{'; '.join(problems)}。这条规则出自：{rule.incident}。{_how_to_fix(rule.rule_id)}"
    )


def test_prompt_rules_registry_is_well_formed() -> None:
    """清单本身的自检：rule_id 不重复，每条规则的 required 至少有一项。"""
    ids = [rule.rule_id for rule in PROMPT_RULES]
    assert len(ids) == len(set(ids)), f"rule_id 重复: {[i for i in ids if ids.count(i) > 1]}"
    empty = [rule.rule_id for rule in PROMPT_RULES if not rule.required and not rule.forbidden]
    assert not empty, f"这些规则既无 required 也无 forbidden，等于没写: {empty}"
