"""台词长度预算：常规靠提示词软引导，仅极端长文做纯确定性完整句熔断（零二次 LLM 缩句）。"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.plan import DecisionPlan, ReplyRoundAttachment
from src.agents.streamer.replyer import (
    RUNAWAY_SPEECH_FLOOR_CHARS,
    Replyer,
    _restore_names_budget_safe,
    is_runaway_speech,
    runaway_limit_for,
)
from src.agents.streamer.speech_guard import fit_to_budget, guard
from src.agents.streamer.tools.reply_tool import ReplyToolProvider
from src.modules.tools.models import ToolInvocation


# 登记（只读穿透，合并时进底账）：接线断言读取内建 Replyer 的台词长度预算
# （agent._replyer._speech_max_chars）；"配置确实传到了回复器"暂无公开只读观察者。
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.llm.payload import Response
from tests.agents.streamer.helpers import make_streamer_agent

_LONG_MULTILINE = (
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


def test_runaway_threshold_and_trigger_conditions() -> None:
    """极端长文熔断阈值取 max(budget * 2, 120)，无换行正常一两句不触发，失控超长或超预算分段才触发。"""
    assert runaway_limit_for(0) == 0
    assert runaway_limit_for(30) == RUNAWAY_SPEECH_FLOOR_CHARS == 120
    assert runaway_limit_for(70) == 140
    assert is_runaway_speech("这是一句自然的两句口播。稍微超过三十字但没失控！", 30) is False
    assert is_runaway_speech("这是一句带换行的第一段话。\n第二段超过三十字的文本在这里继续往后写下去呢！", 30) is True
    assert is_runaway_speech("一" * 121, 30) is True
    assert is_runaway_speech("一" * 200, 0) is False


async def test_normal_two_sentence_speech_above_soft_budget_passes_with_single_llm_call() -> None:
    """正常一两句无换行口播略超软预算但未触达极端熔断阈值时，直接原样保留，绝不发起二次 LLM 缩句。"""
    natural_two_sentences = "我刚从水里爬上岸啦！顺手还捞到一块安山岩，待会儿回去把水车传动轴接上！"
    assert 30 < len(natural_two_sentences) < RUNAWAY_SPEECH_FLOOR_CHARS
    replyer, llm = _replyer(
        Response(success=True, content=natural_two_sentences, request_id="req_natural"),
        speech_max_chars=30,
    )
    result = await replyer.generate(_plan(), [])
    assert result == {"speech": natural_two_sentences, "llm_request_id": "req_natural"}
    assert llm.generate.await_count == 1


async def test_multiline_overflow_is_deterministically_truncated_without_second_llm_call() -> None:
    """含分段换行且超软预算：纯确定性按完整句截断，零额外 LLM 推理调用，保留原请求号。"""
    replyer, llm = _replyer(
        Response(success=True, content=_LONG_MULTILINE, request_id="req_long"),
        speech_max_chars=40,
    )
    result = await replyer.generate(_plan(), [])
    assert result == {
        "speech": "呼，总算爬上来了，整个人湿得跟落汤鸡似的……不过你们看，我手里这块安山岩！",
        "llm_request_id": "req_long",
    }
    assert llm.generate.await_count == 1


async def test_runaway_single_paragraph_is_deterministically_truncated_without_second_llm_call() -> None:
    """单段超长失控文本（> max(budget * 2, 120)）：纯确定性完整句截断，零额外 LLM 推理调用。"""
    runaway = (
        "先说说这波上岸的惊险操作吧！"
        "刚才在水底下绕了一大圈差点没找到出口，还好顺手挖了块安山岩垫脚。"
        "接下来我要沿着河岸往东边走回工坊，顺便把箱子里的铁锭、红石和传动轴全整理一遍。"
        "晚上再去北边山坡插满火把，顺便检查一下水车转速够不够带起整套粉碎机和合成台！"
    )
    assert len(runaway) > RUNAWAY_SPEECH_FLOOR_CHARS
    replyer, llm = _replyer(
        Response(success=True, content=runaway, request_id="req_runaway"),
        speech_max_chars=40,
    )
    result = await replyer.generate(_plan(), [])
    assert result == {
        "speech": "先说说这波上岸的惊险操作吧！",
        "llm_request_id": "req_runaway",
    }
    assert llm.generate.await_count == 1


async def test_budget_zero_leaves_speech_untouched() -> None:
    """预算为 0 = 不限制：只调一次模型，台词原样。"""
    replyer, llm = _replyer(Response(success=True, content=_LONG_MULTILINE, request_id="req_long"), speech_max_chars=0)
    result = await replyer.generate(_plan(), [])
    assert result is not None and result["speech"] == _LONG_MULTILINE and llm.generate.await_count == 1


async def test_turn_prompt_states_the_budget() -> None:
    """软预算先写进本轮要求，多数台词一次就落在预算内。"""
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
    assert replyer.budget_for(mode, has_target=bool(target or reply_to)) == expected_budget
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


async def test_runaway_truncation_keeps_first_sentence_with_merged_reply_to_names() -> None:
    """多位称呼触发极端长文熔断时：确定性截断保留首句称呼，零二次 LLM 呼叫。"""
    runaway_welcome = (
        "欢迎星辰和小橘来到直播间呀！"
        "今天咱们在机械动力存档里折腾水车和矿车，刚才还在矿道里差点被岩浆烫到，结果又回头补了好几块传动轴和齿轮。"
        "待会儿还要继续沿着东边矿洞往下探查矿脉，顺便把工坊里的储物箱、漏斗和传送带全部重新规划整理一遍，晚上再去北边山坡插满火把防刷怪，大家快坐下来一起看看吧！"
    )
    assert len(runaway_welcome) > runaway_limit_for(30 + 40)
    replyer, llm, _ = _modal_replyer(
        Response(success=True, content=runaway_welcome, request_id="req_orig"),
        speech_max_chars=30,
    )
    result = await replyer.generate(
        DecisionPlan(should_reply=True, topic_summary="欢迎进房观众星辰、小橘"),
        [],
        reply_to="星辰、小橘",
    )
    assert result == {
        "speech": (
            "欢迎星辰和小橘来到直播间呀！"
            "今天咱们在机械动力存档里折腾水车和矿车，刚才还在矿道里差点被岩浆烫到，结果又回头补了好几块传动轴和齿轮。"
        ),
        "llm_request_id": "req_orig",
    }
    assert len(result["speech"]) <= 70
    assert "星辰" in result["speech"] and "小橘" in result["speech"]
    assert llm.generate.await_count == 1


async def test_runaway_truncation_restores_missing_keep_names_and_survives_outer_guard() -> None:
    """极端长文熔断裁掉尾句导致必填称呼丢失时，自动把缺失称呼补回句首，且经外层 guard 不再截丢称呼。"""
    # speech_max_chars=15 + TARGET_EXTRA_CHARS=40 => budget=55, runaway_limit=120
    runaway_welcome = (
        "欢迎星辰来到咱们机械动力直播间玩呀！"
        "今天咱们在工坊旁边折腾水车、矿车轨道和齿轮箱，刚刚还回头补了好几块传动轴、漏斗、传送带和火把，顺便把仓库里的铁矿、煤炭和红石全都分类码整齐，待会儿还要去东边河岸继续探索新矿洞呢！"
        "也欢迎小橘和阿杰进来一起看呀！"
    )
    assert len(runaway_welcome) > runaway_limit_for(55)
    replyer, llm, _ = _modal_replyer(
        Response(success=True, content=runaway_welcome, request_id="req_orig"),
        speech_max_chars=15,
    )
    result = await replyer.generate(
        DecisionPlan(should_reply=True, topic_summary="欢迎星辰、小橘、阿杰进房"),
        [],
        keep_names=("星辰", "小橘", "阿杰"),
    )
    assert result is not None
    assert llm.generate.await_count == 1
    verdict = guard(result["speech"], speech_max_chars=55, word_filter=None)
    assert verdict.reason == "passed" and verdict.text is not None
    for name in ("星辰", "小橘", "阿杰"):
        assert name in result["speech"]
        assert name in verdict.text


async def test_keep_names_without_reply_to_omits_game_companion_and_guards_short_draft() -> None:
    """仅传 keep_names 而 reply_to 为空时，提示词绝不渲染「提及的游戏同伴」，且初稿漏名亦自动补回。"""
    replyer, llm, prompt = _modal_replyer(
        Response(success=True, content="欢迎ViewerA来到直播间一起玩呀！", request_id="req_short_draft"),
        speech_max_chars=30,
    )
    result = await replyer.generate(
        DecisionPlan(should_reply=True, topic_summary="欢迎观众ViewerA、ViewerB"),
        [],
        reply_to="",
        keep_names=("ViewerA", "ViewerB"),
    )
    assert result is not None
    assert llm.generate.await_count == 1
    rendered_plan = prompt.render.call_args.kwargs["plan"]
    assert "提及的游戏同伴" not in rendered_plan
    assert "刚在游戏里找过她" not in rendered_plan
    assert "ViewerA、ViewerB" in rendered_plan
    assert "ViewerA" in result["speech"] and "ViewerB" in result["speech"]


async def test_paid_event_batch_keeps_viewer_name_on_runaway_cut_without_game_companion_label() -> None:
    """批次含无 message_id 的付费礼物且触发极端长文熔断时，零二次 LLM 呼叫并保住送礼观众昵称且不渲染游戏同伴模板。"""
    runaway_thanks = (
        "非常感谢观众送出的大礼物呀！"
        "今天咱们在工坊旁边折腾水车和齿轮箱，刚刚还把传送带、漏斗、储物箱和照明火把全部重新摆了一遍，"
        "等会儿继续沿着河岸往东边看看矿洞里还有没有新的矿脉，顺便把门口的农场也一起扩建起来吧！\n"
        "再次谢谢大家的支持呀！"
    )
    replyer, llm, prompt = _modal_replyer(
        Response(success=True, content=runaway_thanks, request_id="req_gift_orig"),
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
    assert llm.generate.await_count == 1
    rendered_plan = prompt.render.call_args.kwargs["plan"]
    assert "提及的游戏同伴" not in rendered_plan
    assert "ViewerAlpha" in rendered_plan
    assert "ViewerAlpha" in result["speech"]


async def test_commentary_purifies_turn_input_and_preserves_item_spec_names() -> None:
    """commentary=True 净化输入面：剥离过时流程单与无关聊天，保留最近口播防复读与专有规格名原貌，且零二次缩句。"""
    natural_commentary = (
        "我正准备把这组带规格编号的抽屉装回墙上，顺便看看旁边的传动轴接得稳不稳！"
        "等会儿装好了就能把背包里的零件全归位啦！"
    )
    assert 30 < len(natural_commentary) < RUNAWAY_SPEECH_FLOOR_CHARS
    replyer, llm, prompt = _modal_replyer(
        Response(success=True, content=natural_commentary, request_id="req_comm"),
        speech_max_chars=30,
    )
    result = await replyer.generate(
        DecisionPlan(should_reply=True, topic_summary="准备把工作区里的专有规格储物设备装到墙上"),
        batch=[
            RoomMessagePayload(
                message_type="danmaku",
                message_id="m_noise",
                user=RoomMessageUser(id="u_n", name="路人"),
                content="无关弹幕噪声",
            )
        ],
        rundown="过时流程单：开场聊天环节",
        game_chat=["[minecraft] 玩家甲：无关游戏公屏噪声"],
        recent_speeches=["刚刚说过的一句防复读台词"],
        reply_to="同伴甲",
        speech_mode="play",
        commentary=True,
    )
    assert result == {"speech": natural_commentary, "llm_request_id": "req_comm"}
    assert llm.generate.await_count == 1
    turn = _rendered(prompt, "amaidesu_replyer")
    assert turn["rundown"] == "（当前无流程单）"
    assert "过时流程单" not in turn["rundown"]
    assert turn["dialogue_prompt"] == "（暂无聊天记录）"
    assert turn["game_chat"] == "" and turn["game_heading"] == "" and turn["game_people_section"] == ""
    assert "刚刚说过的一句防复读台词" in turn["recent_speech_section"]
    assert "同伴甲" in turn["plan"]
    assert "原样保留" in turn["game_turn_rules"]
    assert "进行态与打算态" in turn["game_turn_rules"]
    assert "同伴协助过的成果不说成独自完成" in turn["game_turn_rules"]
    assert "主线打算或向同伴的报备信息" in turn["game_turn_rules"]
    assert "不念原始游戏状态读数" not in turn["game_turn_rules"]


async def test_regular_game_turn_rules_distinguish_telemetry_from_item_spec_names() -> None:
    """常规游戏轮次的 _GAME_TURN_RULES 明确区分原始游戏状态读数与自带规格/型号的物品专有名称。"""
    replyer, _, prompt = _modal_replyer(Response(success=True, content="收到啦！", request_id="req_reg"))
    await replyer.generate(_plan(), [], speech_mode="play", commentary=False)
    rules = _rendered(prompt, "amaidesu_replyer")["game_turn_rules"]
    assert "原始游戏状态读数" in rules
    assert "专有名称必须原样保留" in rules
    assert "不要改写或换算其中的数字" in rules


@pytest.mark.parametrize(
    ("draft", "keep_names", "aliases", "expected"),
    [
        # 回复稿已使用熟人别名亲切称呼：不再句首机械硬拼原账号名
        ("昵称甲来啦，快坐！", ("AccountAlpha",), {"AccountAlpha": ("昵称甲",)}, "昵称甲来啦，快坐！"),
        # 英文别名忽略大小写命中：同样不硬拼
        ("hey alias_a, 欢迎来玩！", ("AccountAlpha",), {"AccountAlpha": ("Alias_A",)}, "hey alias_a, 欢迎来玩！"),
        # 短 ASCII 别名强制词边界："a" 不得在 "haha" 单词片段里误命中，仍补回原账号名
        ("haha 好开心呀！", ("AccountAlpha",), {"AccountAlpha": ("a",)}, "AccountAlpha，haha 好开心呀！"),
        # 短 ASCII 别名处于独立词边界时正常命中，不硬拼原账号名
        ("a 来啦好开心！", ("AccountAlpha",), {"AccountAlpha": ("a",)}, "a 来啦好开心！"),
        # 既没喊原账号名也没喊任何已登记别名：照常补回句首
        ("欢迎来到直播间呀！", ("AccountAlpha",), {"AccountAlpha": ("昵称甲",)}, "AccountAlpha，欢迎来到直播间呀！"),
        # 多人名单：甲已用别名点到、乙漏名 → 只补拼乙，不重复硬拼甲
        (
            "欢迎昵称甲来玩呀！",
            ("AccountAlpha", "AccountBeta"),
            {"AccountAlpha": ("昵称甲",), "AccountBeta": ("昵称乙",)},
            "AccountBeta，欢迎昵称甲来玩呀！",
        ),
    ],
    ids=[
        "alias-hit-no-prepend",
        "alias-case-insensitive",
        "short-ascii-alias-no-substring-false-positive",
        "short-ascii-alias-word-boundary-hit",
        "neither-hit-prepends",
        "multi-user-partial-alias",
    ],
)
async def test_replyer_alias_hit_avoids_prepending_account_name(
    draft: str,
    keep_names: tuple[str, ...],
    aliases: dict[str, tuple[str, ...]],
    expected: str,
) -> None:
    """目标用户的回复稿已包含其已登记熟人别名时视为已点名，避免句首机械硬拼成「账号名，别名！」。"""
    replyer, llm, _ = _modal_replyer(
        Response(success=True, content=draft, request_id="req_alias"),
        speech_max_chars=30,
    )
    result = await replyer.generate(
        DecisionPlan(should_reply=True, topic_summary="欢迎熟人进房"),
        [],
        reply_to="",
        keep_names=keep_names,
        name_aliases=aliases,
    )
    assert result == {"speech": expected, "llm_request_id": "req_alias"}
    assert llm.generate.await_count == 1


def test_restore_names_budget_safe_respects_registered_aliases() -> None:
    """直接调用 _restore_names_budget_safe 时若正文已含已登记别名则原样返回，截断保名亦识别别名。"""
    assert (
        _restore_names_budget_safe(
            "昵称甲来啦！",
            ["AccountAlpha"],
            budget=40,
            keep_names=["AccountAlpha"],
            name_aliases={"AccountAlpha": ["昵称甲"]},
        )
        == "昵称甲来啦！"
    )
    restored = _restore_names_budget_safe(
        "昵称甲欢迎来到机械动力直播间一起玩！今天顺便看看水车和齿轮箱的转速够不够用。",
        ["AccountBeta"],
        budget=28,
        keep_names=["AccountAlpha", "AccountBeta"],
        name_aliases={"AccountAlpha": ["昵称甲"]},
    )
    assert "AccountBeta" in restored
    assert "昵称甲" in restored
    assert "AccountAlpha" not in restored


async def test_reply_tool_passes_registered_aliases_for_paid_viewer_without_prepending() -> None:
    """经 ReplyToolProvider 致谢付费熟人时，透传 name_aliases_provider 查到的别名，初稿喊别名不被硬拼原账号名。"""
    replyer, _, _ = _modal_replyer(
        Response(success=True, content="谢谢昵称甲送的小心心呀！", request_id="req_paid_alias"),
        speech_max_chars=30,
    )
    provider = ReplyToolProvider(
        replyer=replyer,
        name_aliases_provider=lambda names: {"user_a": ["昵称甲"]} if "user_a" in names else {},
    )
    gift_msg = RoomMessagePayload(
        message_type="gift",
        message_id="g_alias_1",
        user=RoomMessageUser(id="u_a", name="user_a"),
        content="",
    )
    exec_result = await provider.invoke(
        ToolInvocation(
            tool_name="streamer_reply",
            arguments={
                "topic_summary": "感谢 user_a 送出礼物",
                "reply_guidance": "亲切致谢",
                "target": "[id:g_alias_1]",
                "emotion": "happy",
                "intensity": 0.6,
                "is_novel": True,
            },
            round_id="r_paid_alias",
            attachment=ReplyRoundAttachment(round_batch=[gift_msg]),
        )
    )
    assert exec_result.success is True
    assert exec_result.structured_content["speech"] == "谢谢昵称甲送的小心心呀！"


async def test_reply_tool_splits_multi_person_reply_to_for_alias_lookup() -> None:
    """ReplyToolProvider 对多人合并的 reply_to（如「A、B」）拆开逐个查别名，避免多人场景查不到别名。"""
    queried_batches: list[list[str]] = []
    runaway_speech = (
        "刚才昵称甲和昵称乙一起帮我搭好了工坊门口的木桥！"
        "今天咱们沿着东边河岸挖了一堆安山岩和铁矿，回来还补了好多齿轮箱、传动轴、漏斗和火把，"
        "待会儿再去仓库把所有的木板和石砖分门别类码整齐，晚上就可以直接扩建二楼温室大棚啦！"
    )
    replyer, _, _ = _modal_replyer(
        Response(success=True, content=runaway_speech, request_id="req_multi_alias"),
        speech_max_chars=25,
    )

    def _lookup(names: list[str]) -> dict[str, list[str]]:
        queried_batches.append(list(names))
        mapping = {"AccountAlpha": ["昵称甲"], "AccountBeta": ["昵称乙"]}
        return {n: mapping[n] for n in names if n in mapping}

    provider = ReplyToolProvider(replyer=replyer, name_aliases_provider=_lookup)
    exec_result = await provider.invoke(
        ToolInvocation(
            tool_name="streamer_reply",
            arguments={
                "topic_summary": "感谢两位同伴帮忙搭桥",
                "reply_guidance": "自然转述",
                "emotion": "happy",
                "intensity": 0.6,
                "is_novel": True,
            },
            round_id="r_multi_alias",
            attachment=ReplyRoundAttachment(reply_to="AccountAlpha、AccountBeta"),
        )
    )
    assert exec_result.success is True
    assert queried_batches == [["AccountAlpha", "AccountBeta"]]
    speech = exec_result.structured_content["speech"]
    assert "昵称甲" in speech and "昵称乙" in speech
    assert "AccountAlpha" not in speech and "AccountBeta" not in speech
