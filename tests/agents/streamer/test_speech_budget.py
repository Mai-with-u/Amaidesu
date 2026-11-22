"""台词长度预算：超出先让模型重说成一句，仍超就截到预算内最后一个完整句子。

提示词里的"一两句"是软要求：本机实测换了两种模型，台词字数中位都在 90 上下、最长 155，
还常分好几段——所以在回复器出口再卡一道。
"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.plan import DecisionPlan
from src.agents.streamer.replyer import Replyer
from src.agents.streamer.speech_guard import fit_to_budget, guard


# 登记（只读穿透，合并时进底账）：接线断言读取内建 Replyer 的台词长度预算
# （agent._replyer._speech_max_chars）；"配置确实传到了回复器"暂无公开只读观察者。
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.llm.payload import Response
from tests.agents.streamer.helpers import make_streamer_agent

_LONG = (
    "呼，总算爬上来了，整个人湿得跟落汤鸡似的……不过你们看，我手里这块安山岩！"
    "其实刚才下水都是战术动作。\n\n诶不过这水底下怎么还有美西螈在游啊？"
)


@pytest.mark.parametrize(
    ("speech", "budget", "expected"),
    [
        ("行吧，顺便洗个澡。", 40, "行吧，顺便洗个澡。"),  # 预算内原样
        ("掉水里了。\n\n行吧。", 40, "掉水里了。行吧。"),  # 分段换行去掉
        ("第一句话说完了。第二句话也说完了。第三句会超出去的。", 18, "第一句话说完了。第二句话也说完了。"),
        ("……诶等一下。我床呢？", 6, "……诶等一下。"),  # 开头的省略号不丢
        ("…………！！", 2, "…………！！"),  # 全是标点切不出句子：原样返回，不截成空串
        (
            "这一整句话本身就已经超出了预算但是没有任何句末标点",
            10,
            "这一整句话本身就已经超出了预算但是没有任何句末标点",
        ),
    ],
)
def testfit_to_budget_keeps_whole_sentences(speech: str, budget: int, expected: str) -> None:
    """只在完整句子之后下刀；第一句本身超预算时整句保留，不截成半截。"""
    assert fit_to_budget(speech, budget) == expected


def _replyer(*responses: Response, speech_max_chars: Optional[int] = 40) -> tuple[Replyer, MagicMock]:
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=list(responses))
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    config: dict[str, Any] = {} if speech_max_chars is None else {"speech_max_chars": speech_max_chars}
    return Replyer(config=config, llm_service=llm, prompt_service=prompt), llm


def _plan() -> DecisionPlan:
    return DecisionPlan(should_reply=True, topic_summary="主播从水里爬上岸")


async def test_long_speech_is_retold_as_one_line() -> None:
    """超预算：只带人设和原话让模型重说一句，用重说的结果和它的请求号。"""
    replyer, llm = _replyer(
        Response(success=True, content=_LONG, request_id="req_long"),
        Response(success=True, content="上岸了……水底下居然有美西螈。", request_id="req_short"),
    )
    result = await replyer.generate(_plan(), [])
    assert result == {"speech": "上岸了……水底下居然有美西螈。", "llm_request_id": "req_short"}
    retell = llm.generate.await_args_list[1]
    instruction = retell.args[0][0]["content"]
    assert _LONG in instruction and "不超过 40 字" in instruction and "不要加原话里没有的事" in instruction
    assert retell.kwargs["system"] == "PROMPT" and retell.kwargs["profile"] == "replyer"


async def test_retell_failure_falls_back_to_cutting_whole_sentences() -> None:
    """重说失败：直接按完整句子截短原台词，照样发声。"""
    replyer, _ = _replyer(
        Response(success=True, content=_LONG, request_id="req_long"),
        Response(success=False, error="timeout"),
    )
    result = await replyer.generate(_plan(), [])
    assert result == {
        "speech": "呼，总算爬上来了，整个人湿得跟落汤鸡似的……不过你们看，我手里这块安山岩！",
        "llm_request_id": "req_long",
    }


async def test_budget_zero_leaves_speech_untouched() -> None:
    """预算为 0 = 不限制：只调一次模型，台词原样。"""
    replyer, llm = _replyer(Response(success=True, content=_LONG, request_id="req_long"), speech_max_chars=0)
    result = await replyer.generate(_plan(), [])
    assert result is not None and result["speech"] == _LONG and llm.generate.await_count == 1


async def test_turn_prompt_states_the_budget() -> None:
    """预算先写进本轮要求，多数台词一次就落在预算内。"""
    replyer, _ = _replyer(Response(success=True, content="好耶", request_id="r"))
    await replyer.generate(_plan(), [])
    turn_kwargs = {call.args[0]: call.kwargs for call in replyer._prompt_service.render.call_args_list}[
        "amaidesu_replyer"
    ]
    assert turn_kwargs["length_rule"] == "不超过 40 字，可以是一两句。"


def test_streamer_wires_speech_budget_into_replyer() -> None:
    """装配期把 speech_max_chars 交给回复器。"""
    agent = make_streamer_agent({"speech_max_chars": 30})
    assert agent._replyer._speech_max_chars == 30


def _modal_replyer(*responses: Response, **config: Any) -> tuple[Replyer, MagicMock, MagicMock]:
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=list(responses))
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    return Replyer(config=config, llm_service=llm, prompt_service=prompt), llm, prompt


def _rendered(prompt: MagicMock, template: str) -> dict[str, Any]:
    return {call.args[0]: call.kwargs for call in prompt.render.call_args_list}[template]


@pytest.mark.parametrize(
    ("mode", "target", "reply_to", "expected_budget", "expected_rule"),
    [
        ("play", None, "", 60, "不超过 60 字，可以是一两句。"),
        ("play", "m1", "", 100, "不超过 100 字，两三句短话以内。"),
        ("play", None, "小夏", 100, "不超过 100 字，两三句短话以内。"),
        ("talk", None, "", 150, "不超过 150 字，两三句。"),
        ("talk", "m1", "", 190, "不超过 190 字，两三句。"),
    ],
    ids=["play", "play-target", "play-reply-to-player", "talk", "talk-target"],
)
async def test_budget_follows_speech_mode_and_target(
    mode: str,
    target: Optional[str],
    reply_to: str,
    expected_budget: int,
    expected_rule: str,
) -> None:
    """边玩边说 60 字、杂谈 150 字；接某位观众或玩家时两档都多给 40 字，容纳起手亮话头且不挤掉正事。"""
    replyer, _, prompt = _modal_replyer(
        Response(success=True, content="好耶", request_id="r"), speech_max_chars=60, talk_speech_max_chars=150
    )
    plan = DecisionPlan(should_reply=True, topic_summary="话头", target=target)
    await replyer.generate(plan, [], reply_to=reply_to, speech_mode=mode)
    turn = _rendered(prompt, "amaidesu_replyer")
    assert turn["length_rule"] == expected_rule
    system = _rendered(prompt, "amaidesu_replyer_system")
    if mode == "talk":
        assert "两三句" in system["speech_style_rule"] and "两三个短句" in turn["pace_rule"]
    else:
        assert "一次只说一件事，说完就停" in system["speech_style_rule"]
        assert "先带出对方问的事再答，答完就停" in system["speech_style_rule"]


async def test_shorten_with_target_keeps_the_opening_hook() -> None:
    """接观众的话却超长：重说要求保留开头顺口带出的问题由头或称呼，删后面的枝节，防止削掉转场锚点。"""
    long_reply = "阿杰问腰上的旋钮能不能拧啊——" + "这个旋钮可以调节麦麦的各种参数，" * 8
    replyer, llm, _ = _modal_replyer(
        Response(success=True, content=long_reply, request_id="r1"),
        Response(success=True, content="阿杰问旋钮能不能拧——拧一下我就变精神。", request_id="r2"),
        speech_max_chars=60,
    )
    await replyer.generate(DecisionPlan(should_reply=True, topic_summary="旋钮", target="m1"), [])
    instruction = llm.generate.await_args_list[1].args[0][0]["content"]
    assert "开头顺口带出的问题由头、称呼或接住对方的词要留着" in instruction and "不超过 100 字" in instruction


async def test_no_game_omits_game_rules_and_section() -> None:
    """没启用游戏：游戏第一人称规矩、游戏聊天一节、游戏读数要求整段省略。"""
    replyer, _, prompt = _modal_replyer(Response(success=True, content="好耶", request_id="r"), has_game=False)
    await replyer.generate(_plan(), [], game_chat=["玩家甲: 你好"], speech_mode="talk")
    system = _rendered(prompt, "amaidesu_replyer_system")
    turn = _rendered(prompt, "amaidesu_replyer")
    assert system["game_rule_line"] == ""
    assert (turn["game_heading"], turn["game_chat"], turn["game_turn_rules"], turn["game_people_section"]) == (
        "",
        "",
        "",
        "",
    )


async def test_persona_profile_is_appended_with_usage_note() -> None:
    replyer, _, prompt = _modal_replyer(
        Response(success=True, content="好耶", request_id="r"), persona_profile="身份与来历：测试设定"
    )
    await replyer.generate(_plan(), [])
    section = _rendered(prompt, "amaidesu_replyer_system")["persona_profile_section"]
    assert section.startswith("\n\n身份与来历：测试设定\n") and "聊别的事时不硬扯" in section


async def test_shorten_falls_back_to_original_cut_when_retell_drops_merged_reply_to_names() -> None:
    """多位进房观众合并称呼超长重说时：指令明确点名保名，若模型仍删成泛称则优先采用保留称呼的原台词裁剪。"""
    long_welcome = (
        "欢迎星辰和小橘来到直播间呀！"
        "今天咱们在机械动力存档里折腾水车和矿车，刚才还在矿道里差点被岩浆烫到，结果又回头补了好几块传动轴和齿轮，"
        "大家快坐下来一起看看这台机器能不能顺利转起来吧！"
    )
    replyer, llm, _ = _modal_replyer(
        Response(success=True, content=long_welcome, request_id="req_orig"),
        Response(success=True, content="欢迎新来的朋友来玩呀，今天在折腾机械动力！", request_id="req_dropped"),
        speech_max_chars=30,
    )
    result = await replyer.generate(
        DecisionPlan(should_reply=True, topic_summary="欢迎新进房观众星辰、小橘"),
        [],
        reply_to="星辰、小橘",
    )
    assert result is not None
    instruction = llm.generate.await_args_list[1].args[0][0]["content"]
    assert "星辰、小橘" in instruction
    assert "星辰" in result["speech"] and "小橘" in result["speech"]
    assert result["speech"] == "欢迎星辰和小橘来到直播间呀！"
    assert result["llm_request_id"] == "req_orig"


async def test_shorten_restores_missing_keep_names_when_both_cuts_lose_them() -> None:
    """原台词前句和重说裁剪都丢掉必填称呼时，把缺失称呼补回句首，绝不把名字挤没。"""
    long_welcome = (
        "今天咱们在机械动力存档里折腾水车和矿车，刚才还在矿道里差点被岩浆烫到，结果又回头补了好几块传动轴和齿轮呢！"
        "欢迎阿杰和小夏来到直播间一起玩呀！"
    )
    replyer, llm, _ = _modal_replyer(
        Response(success=True, content=long_welcome, request_id="req_orig"),
        Response(success=True, content="欢迎新来的小伙伴们一起看折腾水车呀！", request_id="req_short"),
        speech_max_chars=25,
    )
    result = await replyer.generate(
        DecisionPlan(should_reply=True, topic_summary="欢迎新观众"),
        [],
        keep_names=("阿杰", "小夏"),
    )
    assert result is not None
    instruction = llm.generate.await_args_list[1].args[0][0]["content"]
    assert "阿杰、小夏" in instruction
    assert "阿杰" in result["speech"] and "小夏" in result["speech"]
    assert result["speech"].startswith("阿杰、小夏，")


async def test_shorten_uses_retell_directly_when_all_keep_names_are_preserved() -> None:
    """重说结果完整保留全部必填称呼且在预算内时，直接采用重说台词与它的请求号。"""
    long_welcome = (
        "欢迎星辰和小橘来到直播间呀！"
        "今天咱们正在机械动力存档里折腾水车和矿车，刚才还在矿道里差点被岩浆烫到，结果又回头补了好几块传动轴和齿轮，"
        "大家快坐下来一起看看吧！"
    )
    replyer, _, _ = _modal_replyer(
        Response(success=True, content=long_welcome, request_id="req_orig"),
        Response(success=True, content="欢迎星辰、小橘来玩呀，今天在折腾水车呢！", request_id="req_kept"),
        speech_max_chars=30,
    )
    result = await replyer.generate(
        DecisionPlan(should_reply=True, topic_summary="欢迎新观众星辰、小橘"),
        [],
        reply_to="星辰、小橘",
    )
    assert result == {
        "speech": "欢迎星辰、小橘来玩呀，今天在折腾水车呢！",
        "llm_request_id": "req_kept",
    }


async def test_shorten_restored_names_survive_outer_fit_to_budget_guard() -> None:
    """补回缺失称呼后若原本两句贴近预算会超限，须收敛为单句归拢称呼，确保外层 fit_to_budget 不二次截断丢名。"""
    # speech_max_chars=15 + TARGET_EXTRA_CHARS=40 => budget=55
    long_orig = (
        "欢迎星辰来到咱们机械动力二周目直播间玩呀！"
        "今天小橘也正好赶上咱们调试水车和矿道齿轮，待会儿还要看看矿车轨道怎么接上去，大家快一起看看吧！"
    )
    # 重说正好贴近 55 字预算（两句），含星辰（首句）和小橘（次句），漏了阿杰；
    # 若仅把“阿杰，”拼到两句开头会超 55 字，导致外层 fit_to_budget 切掉次句把小橘丢掉
    retell_near_budget = "欢迎星辰来到咱们机械动力二周目直播间玩呀！今天小橘也正好赶上咱们调试水车和矿道齿轮机器呢！"
    replyer, _, _ = _modal_replyer(
        Response(success=True, content=long_orig, request_id="req_orig"),
        Response(success=True, content=retell_near_budget, request_id="req_retell"),
        speech_max_chars=15,
    )
    result = await replyer.generate(
        DecisionPlan(should_reply=True, topic_summary="欢迎星辰、小橘、阿杰进房"),
        [],
        reply_to="星辰、小橘、阿杰",
    )
    assert result is not None
    verdict = guard(result["speech"], speech_max_chars=55, word_filter=None)
    assert verdict.reason == "passed" and verdict.text is not None
    assert len(result["speech"]) <= 55
    for name in ("星辰", "小橘", "阿杰"):
        assert name in result["speech"]
        assert name in verdict.text


async def test_keep_names_without_reply_to_omits_game_companion_and_guards_short_draft() -> None:
    """仅传 keep_names 而 reply_to 为空时，提示词绝不渲染「提及的游戏同伴」，且初稿漏名亦自动补回。"""
    replyer, _, prompt = _modal_replyer(
        Response(success=True, content="欢迎ViewerA来到直播间一起玩呀！", request_id="req_short_draft"),
        speech_max_chars=30,
    )
    result = await replyer.generate(
        DecisionPlan(should_reply=True, topic_summary="欢迎新观众ViewerA、ViewerB"),
        [],
        reply_to="",
        keep_names=("ViewerA", "ViewerB"),
    )
    assert result is not None
    rendered_plan = prompt.render.call_args.kwargs["plan"]
    assert "提及的游戏同伴" not in rendered_plan
    assert "刚在游戏里找过她" not in rendered_plan
    assert "ViewerA、ViewerB" in rendered_plan
    assert "ViewerA" in result["speech"] and "ViewerB" in result["speech"]


async def test_paid_event_batch_keeps_viewer_name_on_shorten_without_game_companion_label() -> None:
    """批次含无 message_id 的付费礼物时，Replyer 自动把送礼观众昵称纳入保名且不渲染游戏同伴模板。"""
    long_thanks = (
        "非常感谢观众送出的大礼物呀！"
        "今天咱们在工坊旁边折腾水车和齿轮箱，刚刚还把传送带、漏斗、储物箱和照明火把全部重新摆了一遍，"
        "等会儿继续一起看看这台机器能不能顺利转起来吧！"
    )
    replyer, llm, prompt = _modal_replyer(
        Response(success=True, content=long_thanks, request_id="req_gift_orig"),
        Response(success=True, content="非常感谢小伙伴送的礼物呀，今天继续折腾水车！", request_id="req_gift_short"),
        speech_max_chars=25,
    )
    gift_msg = RoomMessagePayload(
        message_type="gift",
        message_id="",
        user=RoomMessageUser(id="v_gift_1", name="ViewerAlpha"),
        content="送出 小心心 x1",
    )
    result = await replyer.generate(
        DecisionPlan(should_reply=True, topic_summary="感谢ViewerAlpha送的小心心"),
        [gift_msg],
        reply_to="",
    )
    assert result is not None
    rendered_plan = prompt.render.call_args.kwargs["plan"]
    assert "提及的游戏同伴" not in rendered_plan
    assert "ViewerAlpha" in rendered_plan
    shorten_text = llm.generate.await_args_list[1].args[0][0]["content"]
    assert "ViewerAlpha" in shorten_text
    assert "ViewerAlpha" in result["speech"]
