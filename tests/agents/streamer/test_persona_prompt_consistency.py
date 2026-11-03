"""三条说话通路（Planner、Replyer、嘴）的系统提示词模板口径一致。

被问到是不是真人时坦白自己是 AI 主播、可以俏皮但不能否认——这是法规要求，三条通路各在模板的
固定部分写一份，运营者改人设删不掉。三份分别由不同的代码渲染，任何一份措辞悄悄漂移，各自的测试
都不会报；这里把三份模板放在一起，拿同一个常量逐份核对，报错时点名是哪一份。
其余边界（线下见面、改称呼这类）只来自人设里的边界栏：边界栏清空时，三份模板里都不该再有写死的清单。
嘴的模板这里只读，不改。
"""

from __future__ import annotations

from typing import Any, Dict, Iterator

import pytest

from src.modules.prompts import get_prompt_manager, reset_prompt_manager

#: 三条说话通路共用的 AI 声明：单一事实源，三份模板都必须逐字包含这一句
AI_DECLARATION = "被问到是不是真人时，坦白自己是 AI 主播，可以俏皮，但不能否认。"


@pytest.fixture
def prompts() -> Iterator[Any]:
    reset_prompt_manager()
    yield get_prompt_manager()
    reset_prompt_manager()


def _render_channels(prompts: Any) -> Dict[str, str]:
    """三份系统提示词模板按各自的变量渲染；边界栏一律清空，只剩模板里写死的文字。"""
    return {
        "planner": prompts.render(
            "amaidesu_planner_react",
            bot_name="麦麦",
            personality="活泼",
            behavior_style="积极互动",
            boundaries_rule="",
            game_rules="",
        ),
        "replyer": prompts.render(
            "amaidesu_replyer_system",
            bot_name="麦麦",
            personality="活泼",
            style_section="",
            voice_examples_section="",
            boundaries_rule="",
        ),
        "mouth": prompts.render(
            "amaidesu_mouth",
            bot_name="麦麦",
            personality="活泼",
            style_constraints="口语化",
            max_chars="40",
            emotions="happy",
            long_memory="",
            boundaries_rule="",
        ),
    }


@pytest.mark.parametrize("channel", ["planner", "replyer", "mouth"])
def test_every_speaking_channel_carries_the_same_ai_declaration(prompts: Any, channel: str) -> None:
    """三条通路的模板都逐字写着同一句 AI 声明；边界栏清空也删不掉它。"""
    assert AI_DECLARATION in _render_channels(prompts)[channel]


@pytest.mark.parametrize("channel", ["planner", "replyer", "mouth"])
def test_cleared_boundaries_leave_no_hardcoded_refusal_list(prompts: Any, channel: str) -> None:
    """边界栏清空时，模板里既没有"不答应的事："那一行，也没有写死的越界清单（线下见面、钱和红包、私下联系）。"""
    text = _render_channels(prompts)[channel]
    assert all(phrase not in text for phrase in ("不答应的事：", "线下见面", "钱和红包", "私下联系"))
