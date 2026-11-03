"""Replyer（纯文本台词契约）：正文即台词、人设与本轮数据分模板渲染、system 稳定以保缓存、
聊天记录只带最近几条原文、静默降级、敏感词净化

自训练模型适配后 Replyer 不接收工具、不解析情绪：模型以正文直接输出台词，
情绪与强度由 Planner 经 reply 工具参数决定，由 reply_tool 组装发声数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.plan import DecisionPlan
from src.agents.streamer.replyer import Replyer
from src.agents.streamer.speech_guard import WordFilter
from src.agents.streamer.timeline import CHAPTER_MESSAGE_TYPE, TimelineTurn, note_turn
from src.modules.events.payloads.room import RoomMessageUser
from src.modules.llm.payload import Response
from tests.modules.storage.helpers import make_room_message


@dataclass(frozen=True)
class FakeTurn:
    """live_chat 行的历史视图（鸭子类型替身）。"""

    role: str
    content: str
    sender_name: str = ""
    message_type: str = "danmaku"
    message_id: str = ""


def _plan(should_reply: bool = True, target: str = "m1", topic_summary: str = "打游戏") -> DecisionPlan:
    return DecisionPlan(
        should_reply=should_reply,
        target=target,
        topic_summary=topic_summary,
        reply_guidance="回应夸奖，带点得意",
    )


def _replyer(content: str = "好耶！", *, response: Response | None = None, error: Exception | None = None, **kwargs):
    """构造 Replyer 与 mock LLM / 提示词服务；返回 (replyer, llm, prompt)。"""
    llm = MagicMock()
    if error is not None:
        llm.generate = AsyncMock(side_effect=error)
    else:
        llm.generate = AsyncMock(return_value=response or Response(success=True, content=content, request_id=""))
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    config = {"replyer_llm": "llm", **kwargs.pop("config", {})}
    return Replyer(config=config, llm_service=llm, prompt_service=prompt, **kwargs), llm, prompt


async def test_generate_returns_speech_with_request_id_and_binds_the_replyer_profile() -> None:
    replyer, llm, _ = _replyer(response=Response(success=True, content="好耶！", request_id="req_1"))
    on_delta = MagicMock()
    result = await replyer.generate(_plan(), [], on_delta=on_delta)

    assert result["speech"] == "好耶！" and result["llm_request_id"] == "req_1"
    kwargs = llm.generate.await_args.kwargs
    assert kwargs["profile"] == "replyer"  # 代码显式常量，与 Planner 的 planner 分离
    assert kwargs["system"] == "PROMPT" and "tools" not in kwargs  # 稳定段走 system，表达引擎不发工具面
    assert kwargs["on_delta"] is on_delta  # 流式回调透传


async def test_persona_goes_into_system_template_and_turn_data_into_the_turn_template() -> None:
    """人设整块进 system（说话风格从 user 挪回来、口吻示例一行一句、边界栏一行），user 只放本轮数据。"""
    replyer, _, prompt = _replyer(
        config={
            "bot_name": "麦麦",
            "personality": "活泼开朗，有些调皮",
            "style_constraints": "口语化、简短",
            # 运营者可能写成列表、夹着空行：去掉记号与空行后统一一行一句
            "voice_examples": "诶等一下\n\n- 行吧行吧\n",
            "boundaries": "线下见面",
        }
    )
    await replyer.generate(_plan(), [])

    by_template = {call.args[0]: call.kwargs for call in prompt.render.call_args_list}
    assert by_template["amaidesu_replyer_system"] == {  # 全程稳定的人设块与规矩块可选段
        "bot_name": "麦麦",
        "personality": "活泼开朗，有些调皮",
        "style_section": "\n\n说话风格：口语化、简短",
        "voice_examples_section": "\n\n下面几句只学口吻，不照搬句子：\n- 诶等一下\n- 行吧行吧",
        "boundaries_rule": "\n- 不答应的事：线下见面",
    }

    turn = by_template["amaidesu_replyer"]  # 每轮变化的决策与弹幕；风格与备忘录都不在这里
    assert turn["plan"] is not None and turn["bot_name"] == "麦麦"
    assert turn["rundown"] == "（当前无流程单）"
    assert {"personality", "style_constraints", "notebook_section"}.isdisjoint(turn)


@pytest.mark.parametrize("blank", ["", "  \n "])
async def test_unset_persona_parts_leave_no_section(blank: str) -> None:
    """说话风格、口吻示例、边界栏没写时对应段为空串：模板里整段（整行）省略，不留空行。"""
    replyer, _, prompt = _replyer(config={"style_constraints": blank, "voice_examples": blank, "boundaries": blank})
    await replyer.generate(_plan(), [])

    system = {call.args[0]: call.kwargs for call in prompt.render.call_args_list}["amaidesu_replyer_system"]
    assert (system["style_section"], system["voice_examples_section"], system["boundaries_rule"]) == ("", "", "")


async def test_history_is_merged_into_one_user_turn_and_system_stays_byte_identical() -> None:
    replyer, llm, prompt = _replyer()
    prompt.render = MagicMock(
        side_effect=lambda name, **kwargs: "SYSTEM-STABLE"
        if name.endswith("_system")
        else f"{kwargs.get('dialogue_prompt', '')}|{kwargs.get('plan')}"
    )
    history = [
        FakeTurn(role="user", content="大家好呀", sender_name="小明", message_id="m1"),
        FakeTurn(role="assistant", content="晚上好", message_type="speak", message_id="m2"),
    ]
    await replyer.generate(_plan(topic_summary="聊吉他"), [], history=history)
    await replyer.generate(_plan(target="m9", topic_summary="聊晚饭"), [], history=history)

    first, second = llm.generate.await_args_list
    assert [m["role"] for m in first.args[0]] == ["user"]  # 历史与本轮要求合并进单条 user
    dialogue = first.args[0][0]["content"]
    assert "小明说：大家好呀" in dialogue and "麦麦(你)说：晚上好" in dialogue  # 观众行去"昵称: "前缀，主播行标注(你)
    assert "[id:" not in dialogue  # 消息 ID 只给 Planner 指定目标用，表达侧看到只是噪声
    assert (
        first.kwargs["system"] == second.kwargs["system"] == "SYSTEM-STABLE"
    )  # 缓存契约：前缀缓存命中靠 system 全程稳定
    assert first.args[0][0]["content"] != second.args[0][0]["content"]


# 聊天记录窗口：只带最近几条原文，整份输入不随场长增长


def _danmaku(index: int) -> TimelineTurn:
    return TimelineTurn(role="user", content=f"第{index}条", sender_name="观众", message_id=f"m{index}")


async def _turn_kwargs(replyer: Replyer, prompt: MagicMock, **generate_kwargs: Any) -> Dict[str, Any]:
    """跑一轮生成，取本轮 user 模板收到的渲染参数。"""
    batch = generate_kwargs.pop("batch", [])
    await replyer.generate(_plan(), batch, **generate_kwargs)
    return {call.args[0]: call.kwargs for call in prompt.render.call_args_list}["amaidesu_replyer"]


_CHAPTER = TimelineTurn(role="user", content="[前情提要·第 1 章]\n开场聊了梦", message_type=CHAPTER_MESSAGE_TYPE)


@pytest.mark.parametrize(
    ("history_items", "expected"),
    [
        # 默认：前情提要这类章节层条目不算原文，弹幕、她的话、游戏记录合计只取最近几条
        (3, ["观众说：第7条", "观众说：第8条", "观众说：第9条"]),
        # 回退开关：整份历史（含章节层）原样带上
        (0, ["[前情提要·第 1 章]\n开场聊了梦", *[f"观众说：第{i}条" for i in range(10)]]),
    ],
    ids=["latest-originals", "fallback-whole-history"],
)
async def test_dialogue_keeps_only_the_latest_originals(history_items: int, expected: List[str]) -> None:
    replyer, _, prompt = _replyer(config={"history_items": history_items})
    turn = await _turn_kwargs(replyer, prompt, history=[_CHAPTER, *[_danmaku(i) for i in range(10)]])
    assert turn["dialogue_prompt"] == "\n".join(expected)


async def test_game_reports_are_clipped_and_kept_verbatim() -> None:
    """游戏长汇报只留 80 字正文（行首的时刻与标签保留），短的原样成行，不拆成"某人说"。"""
    report = "交付：" + "木" * 120
    history = [note_turn("游戏·minecraft·report", report, 0), note_turn("游戏·minecraft·里程碑", "做好了床", 0)]
    replyer, _, prompt = _replyer()
    turn = await _turn_kwargs(replyer, prompt, history=history)
    assert turn["dialogue_prompt"] == (f"[游戏·minecraft·report] {report[:80]}…\n[游戏·minecraft·里程碑] 做好了床")


@pytest.mark.parametrize(
    ("batch_ids", "expected"),
    [
        # 本批只有已在最近几条里的：照常截最近 3 条，本批不重复补
        (["m9"], ["第7条", "第8条", "第9条"]),
        # 本批有一条滑出了窗口（m2）、一条还没落库（n1）：两条补进来占用名额，
        # 记录总行数不超过 max(3, 本批 3 条) = 3，窗口相应只剩最近 1 条；滑出的放最前，没落库的追加在末尾
        (["m2", "m9", "n1"], ["第2条", "第9条", "刚到的n1"]),
        # 窗口截短时本批里原本看得到的 m7 也滑了出去：它改为补在最前面，仍然看得到
        (["m7", "n1", "n2", "n3"], ["第7条", "刚到的n1", "刚到的n2", "刚到的n3"]),
        # 本批多于 history_items：总行数以本批条数为上限，本批一条不丢，窗口让出全部名额
        (["n1", "n2", "n3", "n4", "n5"], [f"刚到的n{i}" for i in range(1, 6)]),
    ],
    ids=["already-visible", "slid-out-and-unstored", "shrink-pushes-batch-out", "batch-larger-than-window"],
)
async def test_batch_messages_take_window_slots_and_stay_visible_in_order(
    batch_ids: List[str], expected: List[str]
) -> None:
    """本轮触发她开口的弹幕必须看得到，且占用记录名额：一批弹幕刷屏时窗口变短，整份输入不超预算。"""
    viewer = RoomMessageUser(id="u1", name="观众")
    stored = {f"m{i}": f"第{i}条" for i in range(10)}
    batch = [
        make_room_message(message_id=message_id, content=stored.get(message_id, f"刚到的{message_id}"), user=viewer)
        for message_id in batch_ids
    ]
    replyer, _, prompt = _replyer(config={"history_items": 3})
    turn = await _turn_kwargs(replyer, prompt, batch=batch, history=[_danmaku(i) for i in range(10)])
    assert turn["dialogue_prompt"].splitlines() == [f"观众说：{text}" for text in expected]


#: 单列她更早说过的话那一段的小标题：这几句都滑出了聊天记录，标题点明它们比记录早
_EARLIER_SPEECH = "\n\n## 更早一点你说过的话（比下面的聊天记录早）\n\n"


@pytest.mark.parametrize(
    ("recent", "expected"),
    [
        # 聊天记录里看得到的两句不再重复列，只列滑出窗口的那几句；段尾留一个换行，和下面的记录隔开一行
        (["更早一句", "开场说的", "刚说的", "最新一句"], _EARLIER_SPEECH + "- 更早一句\n- 开场说的\n"),
        # 只看最近 6 句（与发言闸同一份语料）
        ([f"第{i}句" for i in range(9)], _EARLIER_SPEECH + "".join(f"- 第{i}句\n" for i in range(3, 9))),
        # 全都看得到、或没有语料：整段省略
        (["刚说的", "最新一句"], ""),
        (None, ""),
    ],
    ids=["skip-visible", "last-six", "all-visible", "none"],
)
async def test_recent_speeches_list_what_the_window_does_not_show(recent: Optional[List[str]], expected: str) -> None:
    replyer, _, prompt = _replyer()
    history = [TimelineTurn(role="assistant", content="刚说的"), TimelineTurn(role="assistant", content="最新一句")]
    turn = await _turn_kwargs(replyer, prompt, history=history, recent_speeches=recent)
    assert turn["recent_speech_section"] == expected


@pytest.mark.parametrize(
    ("content", "speech"),
    [
        ("  到了到了，机器就在面前～马上开整！  ", "到了到了，机器就在面前～马上开整！"),  # 首尾空白剔除
        ("<think>观众在等，接一句</think>我回来啦", "我回来啦"),  # 闭合的思考段不能念
        (
            '{"speech": "蜂蜜胶还在弄", "emotion": "happy"}',
            '{"speech": "蜂蜜胶还在弄", "emotion": "happy"}',
        ),  # 纯文本契约：不做 JSON 解析
        ("<think>The user is asking me to continue the opening", None),  # 只有没闭合的思考：静默
        ("  ", None),
    ],
)
async def test_content_becomes_speech_verbatim_or_silence(content: str, speech: str | None) -> None:
    replyer, _, _ = _replyer(content)
    result = await replyer.generate(_plan(), [])
    assert (result["speech"] if result else None) == speech


@pytest.mark.parametrize(
    "kwargs",
    [
        {"error": RuntimeError("LLM 挂了")},
        {"response": Response(success=False, content="", error="upstream error")},
    ],
    ids=["exception", "unsuccessful-response"],
)
async def test_llm_failures_degrade_to_silence(kwargs: dict) -> None:
    replyer, _, _ = _replyer(**kwargs)
    assert await replyer.generate(_plan(), []) is None


async def test_should_reply_false_never_calls_the_llm() -> None:
    replyer, llm, _ = _replyer()
    assert await replyer.generate(_plan(should_reply=False), []) is None
    llm.generate.assert_not_called()


def test_word_filter_replaces_drops_and_ignores_case() -> None:
    assert WordFilter(words=["bad"], enabled=False).filter("hello bad world") == (
        "hello bad world",
        False,
    )  # 关闭时不过滤
    assert WordFilter(words=["脏话"], replacement="***").filter("这是脏话测试") == ("这是***测试", True)
    cleaned, dropped = WordFilter(words=["BAD"], replacement="*").filter("hello bad BAD BaD")  # 默认大小写不敏感
    assert dropped is True and "bad" not in cleaned.lower()


@pytest.mark.parametrize(("drop_on_match", "speech"), [(True, None), (False, "这是***测试")])
async def test_word_filter_inside_the_replyer_drops_the_whole_line_or_keeps_the_cleaned_one(
    drop_on_match, speech
) -> None:
    word_filter = WordFilter(words=["脏话"], replacement="***", drop_on_match=drop_on_match)
    replyer, _, _ = _replyer("这是脏话测试", word_filter=word_filter)
    result = await replyer.generate(_plan(), [])
    assert (result["speech"] if result else None) == speech
