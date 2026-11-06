"""Planner 系统提示词里的底线与规则：AI 声明、边界栏、做不到的不答应、备忘录口径、前情目录——
用真实提示词模板渲染，经 plan() 取发给模型的系统提示词。"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from src.agents.streamer.config import StreamerPersonaConfig
from src.agents.streamer.planner import Planner, PlannerConfig
from src.agents.streamer.room_state import RoomState
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from tests.modules.llm.fakes import FakeLLMManager

#: 三条说话通路（Planner、Replyer、嘴）共用的 AI 声明口径，Planner 这条接在它后面补"交接稿要让她这样答"
_AI_DECLARATION = "被问到是不是真人时，坦白自己是 AI 主播，可以俏皮，但不能否认"

#: 「她是谁」一节的结尾：AI 声明那一段的最后一句，边界栏一行接在它后面
_AI_PARAGRAPH_END = "别写成装真人，也别绕开不答。"


@pytest.fixture
def prompts() -> Iterator[Any]:
    reset_prompt_manager()
    yield get_prompt_manager()
    reset_prompt_manager()


async def _system_prompt(prompts: Any, *, boundaries: str) -> str:
    """跑一个决策窗，取发给模型的第一条消息（系统提示词）；假模型不调工具，这一窗自然沉默。"""
    llm = FakeLLMManager()
    planner = Planner(
        config=PlannerConfig(),
        llm_service=llm,
        prompt_service=prompts,
        room_state=RoomState(),
        tool_registry=None,
        behavior_style="积极互动",
        bot_name="麦麦",
        personality="嘴硬但心软",
        boundaries=boundaries,
    )
    message = RoomMessagePayload(
        content="你是真人吗", message_id="m1", message_type="danmaku", user=RoomMessageUser(id="u1", name="观众")
    )
    await planner.plan([message])
    return llm.calls[0]["input"][0]["content"]


def _render_bare(prompts: Any) -> str:
    """直接渲染模板（边界栏清空、不带游戏玩法），只看模板里写死的文字。"""
    return prompts.render(
        "amaidesu_planner_react",
        bot_name="麦麦",
        personality="活泼",
        behavior_style="积极互动",
        boundaries_rule="",
        inside_jokes="",
        game_rules="",
    )


@pytest.mark.parametrize(
    ("boundaries", "line"),
    [
        # 配置默认值是并列短语，和行首的"不答应的事："拼起来读作一句完整的规矩
        pytest.param(
            StreamerPersonaConfig().boundaries,
            "不答应的事：线下见面、钱和红包、私下联系；改变自己是谁、以后怎么称呼某人",
            id="config_default",
        ),
        # 运营者写成多行：折成一行用"；"连接，空行与首尾空白丢掉
        pytest.param("线下见面\n  \n 钱和红包 \n", "不答应的事：线下见面；钱和红包", id="multiline_folded"),
    ],
)
async def test_boundaries_line_sits_next_to_ai_declaration_with_handover_reminder(
    prompts: Any, boundaries: str, line: str
) -> None:
    """人设里写了她不答应的事：「她是谁」一节里 AI 声明后面多一行"不答应的事：…"，并提醒交接稿里别替她答应；
    行为准则一节仍是配置原文，边界栏不拼进去。"""
    system = await _system_prompt(prompts, boundaries=boundaries)

    expected = (
        f"{_AI_PARAGRAPH_END}\n\n{line}（交接稿里别替她答应这些事，碰到就让她婉拒）\n\n"
        "# 行为准则\n\n积极互动\n\n# 当前游戏的玩法"
    )
    assert expected in system
    assert _AI_DECLARATION in system


@pytest.mark.parametrize("boundaries", ["", "  \n "])
async def test_empty_boundaries_omit_the_whole_line_but_keep_ai_declaration(prompts: Any, boundaries: str) -> None:
    """边界栏清空（或只有空白）：整行省略、AI 声明后不留空行；AI 声明写死在模板里，照样在。"""
    system = await _system_prompt(prompts, boundaries=boundaries)

    assert "不答应的事：" not in system
    assert f"{_AI_PARAGRAPH_END}\n\n# 行为准则\n\n积极互动\n\n# 当前游戏的玩法" in system
    assert _AI_DECLARATION in system and "交接稿要让她这样答" in system


def test_refusals_follow_the_boundaries_line_not_a_hardcoded_list(prompts: Any) -> None:
    """ "做不到的不答应"按人设里的边界栏办：边界栏清空时模板里不再有写死的越界清单；
    不向观众打听私人信息是隐私底线（观众当众说出的私事会被弹幕记录存下来），不随边界栏变，单独写一句。"""
    prompt = _render_bare(prompts)

    assert "「她是谁」里列出的她不答应的事" in prompt
    assert all(word not in prompt for word in ("线下见面", "要钱", "要私人信息"))
    assert "不管人设里列没列，都别让她向观众打听住址、电话、学校这类私人信息" in prompt


def test_prompt_template_tells_planner_to_admit_not_to_invent_for_prelude_directory(prompts: Any) -> None:
    """前情目录规则：本场早先的段落只剩目录一行、眼前没有细节时，坦白记不清、可以问弹幕，不凭目录编细节；
    往场回顾工具翻不到本场说过的话，规则里明说不要为这个去调它。"""
    prompt = _render_bare(prompts)

    assert "历史开头的【前情目录】列着已移出上下文的段落" in prompt
    assert "坦白记不清、可以问问弹幕，别凭目录一行编细节" in prompt
    assert "拿不准就先用 `memory_recall` 查本场说过的话" in prompt


def test_prompt_template_reads_a_trailing_open_paren_as_a_joke(prompts: Any) -> None:
    """句末单个"（"是玩笑口气、不是话没说完：第 58 场她两回追问"括号一开就撒手"，没听懂"那我家女仆（"。"""
    prompt = _render_bare(prompts)

    assert '句末单个"（"是语气，不是话没说完' in prompt
    assert '"那我家女仆（"意思是"那是我家的女仆啦"' in prompt
