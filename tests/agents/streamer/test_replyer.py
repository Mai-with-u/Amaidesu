"""Replyer（纯文本台词契约）：正文即台词、人设与本轮数据分模板渲染、system 稳定以保缓存、静默降级、敏感词净化

自训练模型适配后 Replyer 不接收工具、不解析情绪：模型以正文直接输出台词，
情绪与强度由 Planner 经 reply 工具参数决定，由 reply_tool 组装发声数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.plan import DecisionPlan
from src.agents.streamer.replyer import Replyer
from src.agents.streamer.speech_guard import WordFilter
from src.modules.llm.payload import Response


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
    replyer, _, prompt = _replyer(
        config={"bot_name": "麦麦", "personality": "活泼开朗，有些调皮", "style_constraints": "口语化、简短"}
    )
    await replyer.generate(_plan(), [])

    by_template = {call.args[0]: call.kwargs for call in prompt.render.call_args_list}
    system = by_template["amaidesu_replyer_system"]  # 全程稳定的人设字段
    assert (system["personality"], system["bot_name"]) == (
        "活泼开朗，有些调皮",
        "麦麦",
    )
    # 模板正文已硬编码对观众的统一称呼，变量退役；再多传会被渲染期硬错
    assert "audience_salutation" not in system
    assert "plan" not in system and "dialogue_prompt" not in system

    turn = by_template["amaidesu_replyer"]  # 每轮变化的决策、弹幕与风格约束
    assert turn["plan"] is not None and turn["style_constraints"] == "口语化、简短" and turn["bot_name"] == "麦麦"
    assert turn["rundown"] == "（当前无流程单）"
    assert "personality" not in turn and "audience_salutation" not in turn


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
    assert (
        "小明说：大家好呀 [id:m1]" in dialogue and "麦麦(你)说：晚上好" in dialogue
    )  # 观众行去"昵称: "前缀，主播行标注(你)
    assert (
        first.kwargs["system"] == second.kwargs["system"] == "SYSTEM-STABLE"
    )  # 缓存契约：前缀缓存命中靠 system 全程稳定
    assert first.args[0][0]["content"] != second.args[0][0]["content"]


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
