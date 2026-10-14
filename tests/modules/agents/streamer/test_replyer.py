"""Replyer 单元测试（纯文本台词契约：generate + payload.Response.content）。

自训练模型适配后 Replyer 不接收工具、不解析情绪：模型以正文直接输出台词，
情绪与强度由 Planner 经 reply 工具参数决定，由 reply_tool 组装发声数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.plan import DecisionPlan
from src.agents.streamer.replyer import WordFilter, Replyer
from src.modules.llm.payload import Response


@dataclass(frozen=True)
class FakeTurn:
    """live_chat 行的历史视图（同 _LiveChatTurn 全字段，鸭子类型替身）。"""

    role: str
    content: str
    sender_name: str = ""
    message_type: str = "danmaku"
    message_id: str = ""


def _make_plan(
    should_reply: bool = True,
    target: str = "m1",
    topic_summary: str = "打游戏",
) -> DecisionPlan:
    return DecisionPlan(
        should_reply=should_reply,
        target=target,
        topic_summary=topic_summary,
        reply_guidance="回应夸奖，带点得意",
        confidence=0.9,
    )


def _make_llm_response(
    *,
    content: str = "",
    success: bool = True,
    error: str | None = None,
    request_id: str = "",
) -> Response:
    """构造 payload.Response（generate 返回值；纯文本契约只消费 content）。"""
    return Response(
        success=success,
        content=content,
        error=error,
        request_id=request_id,
    )


def _make_replyer(
    llm_response: Response | None = None,
    llm_side_effect: Exception | None = None,
    config: dict | None = None,
    word_filter: WordFilter | None = None,
    tool_registry=None,
):
    """构造 Replyer + mock LLM。

    Returns:
        (replyer, llm_mock, prompt_mock)
    """
    llm = MagicMock()
    if llm_side_effect is not None:
        llm.generate = AsyncMock(side_effect=llm_side_effect)
    else:
        resp = llm_response or _make_llm_response()
        llm.generate = AsyncMock(return_value=resp)

    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")

    cfg = {"replyer_llm": "llm"}
    if config:
        cfg.update(config)

    r = Replyer(
        config=cfg,
        llm_service=llm,
        prompt_service=prompt,
        tool_registry=tool_registry,
        word_filter=word_filter,
    )
    return r, llm, prompt


class TestReplyerGenerate:
    """基础生成行为。"""

    @pytest.mark.asyncio
    async def test_replyer_generates_result(self) -> None:
        """mock LLM 正文返回台词 → 生成含 speech 的 dict，请求 ID 透传。"""
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(content="好耶！", request_id="req_1"),
        )
        plan = _make_plan()
        result = await r.generate(plan, [])

        assert result is not None
        assert isinstance(result, dict)
        assert result["speech"] == "好耶！"
        assert result["llm_request_id"] == "req_1"

    @pytest.mark.asyncio
    async def test_replyer_persona_in_system_and_data_in_turn_input(self) -> None:
        """稳定段渲染入参含构造 config 注入的人设；变化段渲染入参含决策与弹幕。

        system 模板（amaidesu_replyer_system）承载全程稳定的人设字段，
        本轮输入模板（amaidesu_replyer）承载每轮变化的决策、弹幕与风格约束。
        """
        r, _llm, prompt = _make_replyer(
            llm_response=_make_llm_response(content="好耶！"),
            config={
                "bot_name": "麦麦",
                "personality": "活泼开朗，有些调皮",
                "style_constraints": "口语化、简短",
            },
        )
        plan = _make_plan()
        await r.generate(plan, [])

        by_template = {call.args[0]: call.kwargs for call in prompt.render.call_args_list}
        system_kwargs = by_template["amaidesu_replyer_system"]
        assert system_kwargs["personality"] == "活泼开朗，有些调皮"
        assert system_kwargs["bot_name"] == "麦麦"
        assert system_kwargs["audience_salutation"] == "大家"
        assert "plan" not in system_kwargs and "dialogue_prompt" not in system_kwargs

        user_kwargs = by_template["amaidesu_replyer"]
        assert user_kwargs["plan"] is not None
        assert user_kwargs["style_constraints"] == "口语化、简短"
        assert user_kwargs["bot_name"] == "麦麦"
        assert user_kwargs["rundown"] == "（当前无流程单）"
        assert "personality" not in user_kwargs and "audience_salutation" not in user_kwargs

    @pytest.mark.asyncio
    async def test_replyer_history_merged_into_turn_input(self) -> None:
        """对话历史与本轮要求合并进单条 user：历史逐条映射为对话行，决策收尾。

        观众行去掉"昵称: "前缀改为"昵称说："；主播行标注"(你)"。
        """
        r, llm, prompt = _make_replyer(
            llm_response=_make_llm_response(content="晚上好"),
        )
        prompt.render = MagicMock(
            side_effect=lambda name, **kwargs: "SYSTEM"
            if name.endswith("_system")
            else kwargs.get("dialogue_prompt", "")
        )
        history = [
            FakeTurn(role="user", content="大家好呀", sender_name="小明", message_id="m1"),
            FakeTurn(role="assistant", content="晚上好", message_type="speak", message_id="m2"),
        ]
        await r.generate(_make_plan(), [], history=history)

        messages = llm.generate.await_args.args[0]
        assert [m["role"] for m in messages] == ["user"]
        dialogue = messages[0]["content"]
        assert "小明说：大家好呀 [id:m1]" in dialogue
        assert "麦麦(你)说：晚上好" in dialogue

    @pytest.mark.asyncio
    async def test_replyer_system_stable_across_requests(self) -> None:
        """缓存契约：system 稳定段两次请求逐字一致，只有本轮输入 user 变化。

        请求布局是单条 system + 单条 user；前缀缓存命中靠 system 全程稳定。
        """
        r, llm, prompt = _make_replyer(
            llm_response=_make_llm_response(content="好"),
        )
        # 渲染 mock 按模板区分返回值，使两次请求的本轮输入内容可区分
        prompt.render = MagicMock(
            side_effect=lambda name, **kwargs: "SYSTEM-STABLE"
            if name.endswith("_system")
            else f"TURN<{kwargs.get('plan')}>"
        )
        history = [
            FakeTurn(role="user", content="大家好呀", sender_name="小明", message_id="m1"),
        ]

        await r.generate(_make_plan(target="m1", topic_summary="聊吉他"), [], history=history)
        await r.generate(_make_plan(target="m9", topic_summary="聊晚饭"), [], history=history)

        first_msgs = llm.generate.await_args_list[0].args[0]
        second_msgs = llm.generate.await_args_list[1].args[0]
        first_system = llm.generate.await_args_list[0].kwargs["system"]
        second_system = llm.generate.await_args_list[1].kwargs["system"]
        assert first_system == second_system == "SYSTEM-STABLE"
        assert first_msgs[0]["content"] != second_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_replyer_passes_system_without_tools(self) -> None:
        """generate 以 system 参数携带稳定段；表达引擎不发送工具面。"""
        r, llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(content="好耶！"),
        )
        await r.generate(_make_plan(), [])

        kwargs = llm.generate.await_args.kwargs
        assert kwargs.get("system") == "PROMPT"
        assert "tools" not in kwargs

    @pytest.mark.asyncio
    async def test_replyer_uses_replyer_profile(self) -> None:
        """断言 generate 绑定 replyer profile（代码显式常量，与 Planner 的 planner 分离）。"""
        r, llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(content="好耶！"),
        )
        plan = _make_plan()
        await r.generate(plan, [])

        assert llm.generate.await_args.kwargs.get("profile") == "replyer"

    @pytest.mark.asyncio
    async def test_replyer_registry_tools_not_consumed(self) -> None:
        """表达引擎无工具面：registry 工具不进入表达会话，也不被查询。"""
        from src.modules.tools.models import ToolSpec
        from unittest.mock import MagicMock

        registry = MagicMock()
        registry.list_tools.return_value = [
            ToolSpec(name="warudo.wave", description="挥手", parameters_schema=None, provider="warudo"),
            ToolSpec(name="minecraft_get_state", description="查状态", parameters_schema=None, provider="minecraft"),
        ]
        r, llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(content="好耶！"),
            tool_registry=registry,
        )
        plan = _make_plan()
        await r.generate(plan, [])

        kwargs = llm.generate.await_args.kwargs
        assert "tools" not in kwargs
        registry.list_tools.assert_not_called()

    @pytest.mark.asyncio
    async def test_replyer_on_delta_passthrough(self) -> None:
        """on_delta 回调透传给 generate（流式行为不变）。"""
        r, llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(content="好耶！"),
        )
        plan = _make_plan()
        on_delta = MagicMock()
        await r.generate(plan, [], on_delta=on_delta)

        assert llm.generate.await_args.kwargs.get("on_delta") is on_delta

    @pytest.mark.asyncio
    async def test_replyer_plain_text_becomes_speech(self) -> None:
        """正文即台词（自训练模型纯文本输出契约），首尾空白剔除。"""
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(content="  到了到了，机器就在面前～马上开整！  "),
        )
        result = await r.generate(_make_plan(), [])

        assert result is not None
        assert result["speech"] == "到了到了，机器就在面前～马上开整！"

    @pytest.mark.asyncio
    async def test_replyer_plain_text_drops_thinking(self) -> None:
        """正文里的思考段不能念：闭合的去掉留下台词；只有没闭合的思考就照常静默。"""
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(content="<think>观众在等，接一句</think>我回来啦"),
        )
        assert (await r.generate(_make_plan(), []))["speech"] == "我回来啦"

        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(content="<think>The user is asking me to continue the opening"),
        )
        assert await r.generate(_make_plan(), []) is None

    @pytest.mark.asyncio
    async def test_replyer_content_taken_verbatim(self) -> None:
        """纯文本契约：正文不做 JSON 解析，原样成为台词。"""
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(content='{"speech": "蜂蜜胶还在弄", "emotion": "happy"}'),
        )
        result = await r.generate(_make_plan(), [])

        assert result is not None
        assert result["speech"] == '{"speech": "蜂蜜胶还在弄", "emotion": "happy"}'

    @pytest.mark.asyncio
    async def test_replyer_empty_speech_silent(self) -> None:
        """正文为空 → silent 降级返回 None。"""
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(content="  "),
        )
        plan = _make_plan()
        result = await r.generate(plan, [])

        assert result is None

    @pytest.mark.asyncio
    async def test_replyer_llm_failure_silent(self) -> None:
        """LLM 调用抛异常 → 返回 None（silent 降级），不抛出。"""
        r, _llm, _prompt = _make_replyer(llm_side_effect=RuntimeError("LLM 挂了"))
        plan = _make_plan()
        result = await r.generate(plan, [])

        assert result is None

    @pytest.mark.asyncio
    async def test_replyer_llm_response_failure_silent(self) -> None:
        """payload.Response.success=False → 返回 None（silent 降级）。"""
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(success=False, error="upstream error"),
        )
        plan = _make_plan()
        result = await r.generate(plan, [])

        assert result is None

    @pytest.mark.asyncio
    async def test_replyer_should_reply_false_skips(self) -> None:
        """plan.should_reply=False → 不调 LLM，直接返回 None。"""
        r, llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(content="好耶！"),
        )
        plan = _make_plan(should_reply=False)
        result = await r.generate(plan, [])

        assert result is None
        llm.generate.assert_not_called()


class TestReplyerWordFilter:
    """敏感词净化：原 output/pipelines/word_filter 逻辑 verbatim 归此地。"""

    def test_word_filter_disabled_passes_through(self) -> None:
        """filter.enabled=False 时不过滤。"""
        flt = WordFilter(words=["bad"], enabled=False)
        cleaned, dropped = flt.filter("hello bad world")
        assert cleaned == "hello bad world"
        assert dropped is False

    def test_word_filter_replace_word(self) -> None:
        """filter.enabled=True + words=["脏话"] → 替换为 replacement。"""
        flt = WordFilter(words=["脏话"], replacement="***")
        cleaned, dropped = flt.filter("这是脏话测试")
        assert cleaned == "这是***测试"
        assert dropped is True

    @pytest.mark.asyncio
    async def test_word_filter_drop_on_match(self) -> None:
        """drop_on_match=True 时整条返回 None。"""
        flt = WordFilter(
            words=["脏话"],
            replacement="***",
            drop_on_match=True,
        )
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(content="这是脏话测试"),
            word_filter=flt,
        )
        plan = _make_plan()
        result = await r.generate(plan, [])

        assert result is None

    @pytest.mark.asyncio
    async def test_word_filter_replace_keep_result(self) -> None:
        """drop_on_match=False（默认）时净化后保留 result。"""
        flt = WordFilter(words=["脏话"], replacement="***")
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(content="这是脏话测试"),
            word_filter=flt,
        )
        plan = _make_plan()
        result = await r.generate(plan, [])

        assert result is not None
        assert result["speech"] == "这是***测试"

    def test_word_filter_case_insensitive(self) -> None:
        """默认 case_sensitive=False 时大小写不敏感。"""
        flt = WordFilter(words=["BAD"], replacement="*")
        cleaned, dropped = flt.filter("hello bad BAD BaD")
        assert dropped is True
        assert "bad" not in cleaned.lower() or "*" in cleaned
