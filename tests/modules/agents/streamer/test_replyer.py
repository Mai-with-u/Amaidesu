"""Replyer 单元测试（中立 payload 契约：generate + payload.Response）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.plan import DecisionPlan
from src.agents.streamer.replyer import WordFilter, Replyer
from src.modules.llm.payload import Response, ToolCall


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


def _tool_call_reply(
    speech: str = "好耶！",
    emotion: str = "happy",
    call_id: str = "call_1",
) -> ToolCall:
    """构造一个 reply tool_call（中立 payload 扁平形状，arguments 已解析）。"""
    return ToolCall(
        name="reply",
        arguments={"speech": speech, "emotion": emotion},
        id=call_id,
    )


def _tool_call_action(
    name: str = "warudo.wave",
    parameters: Optional[dict] = None,
    call_id: str = "call_2",
) -> ToolCall:
    """构造一个动作工具 tool_call（中立 payload 扁平形状）。"""
    return ToolCall(
        name=name,
        arguments=parameters or {},
        id=call_id,
    )


def _make_llm_response(
    *,
    tool_calls: Optional[List[ToolCall]] = None,
    success: bool = True,
    error: Optional[str] = None,
) -> Response:
    """构造 payload.Response（generate 返回值）。"""
    return Response(
        success=success,
        content="",
        tool_calls=tool_calls or [],
        error=error,
    )


def _make_replyer(
    llm_response: Optional[Response] = None,
    llm_side_effect: Optional[Exception] = None,
    action_tools=None,
    config: Optional[dict] = None,
    word_filter: Optional[WordFilter] = None,
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
    # 旧入口不应被新代码触碰
    llm.call_tools = AsyncMock()

    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")

    tool_registry = tool_registry if tool_registry is not None else None
    if action_tools is not None:
        tool_registry = MagicMock()
        tool_registry.list_tools = MagicMock(return_value=action_tools)

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
        """mock LLM 返回 reply tool_call → 生成包含 speech/emotion 的 dict。"""
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(
                tool_calls=[_tool_call_reply(speech="好耶！", emotion="happy")],
            ),
        )
        plan = _make_plan()
        result = await r.generate(plan, [])

        assert result is not None
        assert isinstance(result, dict)
        assert result["speech"] == "好耶！"
        assert result["emotion"]["name"] == "happy"
        assert result["actions"] == []
        assert result["metadata"]["target"] == "m1"
        assert result["metadata"]["topic_summary"] == "打游戏"

    @pytest.mark.asyncio
    async def test_replyer_persona_in_system_and_data_in_turn_input(self) -> None:
        """稳定段渲染入参含构造 config 注入的人设四件套；变化段渲染入参含决策与弹幕。

        system 模板（amaidesu_replyer_system）承载人设/风格等全程稳定段，
        本轮输入模板（amaidesu_replyer）只承载每轮变化的决策与弹幕。
        """
        r, _llm, prompt = _make_replyer(
            llm_response=_make_llm_response(
                tool_calls=[_tool_call_reply()],
            ),
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
        assert system_kwargs["style_constraints"] == "口语化、简短"
        assert system_kwargs["bot_name"] == "麦麦"
        assert system_kwargs["audience_salutation"] == "大家"
        assert "plan" not in system_kwargs and "danmaku_batch" not in system_kwargs

        user_kwargs = by_template["amaidesu_replyer"]
        assert user_kwargs["plan"] is not None
        assert user_kwargs["danmaku_batch"] is not None
        assert user_kwargs["rundown"] == "（当前无流程单）"
        assert "personality" not in user_kwargs and "bot_name" not in user_kwargs
        # Y 模型：移除 $action_list（actions 走 tool_calls）
        assert all("action_list" not in kwargs for kwargs in by_template.values())

    @pytest.mark.asyncio
    async def test_replyer_history_as_native_messages(self) -> None:
        """对话历史经 canonical 走原生消息通道：历史消息在前逐条映射，本轮输入 user 消息收尾。"""
        r, llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(tool_calls=[_tool_call_reply()]),
        )
        history = [
            FakeTurn(role="user", content="大家好呀", sender_name="小明", message_id="m1"),
            FakeTurn(role="assistant", content="晚上好", message_type="speak", message_id="m2"),
        ]
        await r.generate(_make_plan(), [], history=history)

        messages = llm.generate.await_args.args[0]
        assert [m["role"] for m in messages] == ["user", "assistant", "user"]
        assert messages[0] == {"role": "user", "content": "小明: 大家好呀 [id:m1]"}
        assert messages[1] == {"role": "assistant", "content": "晚上好"}
        assert messages[-1]["role"] == "user"
        assert "PROMPT" in messages[-1]["content"]

    @pytest.mark.asyncio
    async def test_replyer_message_prefix_byte_stable_across_requests(self) -> None:
        """缓存契约：同一段历史在两次请求中逐字一致，只有末条本轮输入变化。

        system + 历史构成请求稳定前缀，供应商前缀缓存据此命中；本轮输入
        （决策/弹幕/流程单）只允许出现在序列尾。
        """
        r, llm, prompt = _make_replyer(
            llm_response=_make_llm_response(tool_calls=[_tool_call_reply()]),
        )
        # 渲染 mock 按模板区分返回值，使两次请求的本轮输入内容可区分
        prompt.render = MagicMock(
            side_effect=lambda name, **kwargs: "SYSTEM-STABLE"
            if name.endswith("_system")
            else f"TURN<{kwargs.get('plan')}>"
        )
        history = [
            FakeTurn(role="user", content="大家好呀", sender_name="小明", message_id="m1"),
            FakeTurn(role="assistant", content="晚上好", message_type="speak", message_id="m2"),
        ]

        await r.generate(_make_plan(target="m1", topic_summary="聊吉他"), [], history=history)
        await r.generate(_make_plan(target="m9", topic_summary="聊晚饭"), [], history=history)

        first = llm.generate.await_args_list[0].args[0]
        second = llm.generate.await_args_list[1].args[0]
        assert second[:-1] == first[:-1]
        assert first[-1]["content"] != second[-1]["content"]

    @pytest.mark.asyncio
    async def test_replyer_passes_system_and_reply_tools(self) -> None:
        """generate 以 system 参数携带稳定段；工具面仅 reply 一项。"""
        r, llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(tool_calls=[_tool_call_reply()]),
        )
        await r.generate(_make_plan(), [])

        kwargs = llm.generate.await_args.kwargs
        assert kwargs.get("system") == "PROMPT"
        assert [t["name"] for t in kwargs["tools"]] == ["reply"]

    @pytest.mark.asyncio
    async def test_replyer_uses_replyer_profile(self) -> None:
        """断言 generate 绑定 replyer profile（代码显式常量，与 Planner 的 planner 分离）。"""
        r, llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(tool_calls=[_tool_call_reply()]),
        )
        plan = _make_plan()
        await r.generate(plan, [])

        assert llm.generate.await_args.kwargs.get("profile") == "replyer"

    @pytest.mark.asyncio
    async def test_replyer_only_reply_tool_visible(self) -> None:
        """表达引擎无工具面：LLM 只见 reply，registry 工具不进入表达会话。"""
        from src.modules.tools.models import ToolSpec
        from unittest.mock import MagicMock

        registry = MagicMock()
        registry.list_tools.return_value = [
            ToolSpec(name="warudo.wave", description="挥手", parameters_schema=None, provider="warudo"),
            ToolSpec(name="minecraft_get_state", description="查状态", parameters_schema=None, provider="minecraft"),
        ]
        r, llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(tool_calls=[_tool_call_reply()]),
            tool_registry=registry,
        )
        plan = _make_plan()
        await r.generate(plan, [])

        kwargs = llm.generate.await_args.kwargs
        tool_names = [t["name"] for t in kwargs["tools"]]
        assert tool_names == ["reply"]
        registry.list_tools.assert_not_called()

    @pytest.mark.asyncio
    async def test_replyer_on_delta_passthrough(self) -> None:
        """on_delta 回调透传给 generate（流式行为不变）。"""
        r, llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(tool_calls=[_tool_call_reply()]),
        )
        plan = _make_plan()
        on_delta = MagicMock()
        await r.generate(plan, [], on_delta=on_delta)

        assert llm.generate.await_args.kwargs.get("on_delta") is on_delta

    @pytest.mark.asyncio
    async def test_replyer_ignores_non_reply_tool_calls(self) -> None:
        """LLM 偶发非 reply 调用（工具面只有 reply，理论不该发生）→ 忽略，actions 恒空。"""
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(
                tool_calls=[
                    _tool_call_reply(speech="好的", emotion="excited"),
                    _tool_call_action(name="warudo.wave", parameters={}),
                ],
            ),
        )
        plan = _make_plan()
        result = await r.generate(plan, [])

        assert result is not None
        assert result["speech"] == "好的"
        assert result["emotion"]["name"] == "excited"
        assert result["actions"] == []

    @pytest.mark.asyncio
    async def test_replyer_invalid_emotion_degrades(self) -> None:
        """非法 emotion（不在 12 枚举内）→ 降级为 neutral，speech 保留。"""
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(
                tool_calls=[_tool_call_reply(speech="嗯嗯", emotion="这不是情绪")],
            ),
        )
        plan = _make_plan()
        result = await r.generate(plan, [])

        assert result is not None
        assert result["emotion"]["name"] == "neutral"
        assert result["speech"] == "嗯嗯"

    @pytest.mark.asyncio
    async def test_replyer_no_reply_tool_call_silent(self) -> None:
        """LLM 返回非 reply 的工具调用（无 reply call）→ silent 降级返回 None。"""
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(
                tool_calls=[_tool_call_action(name="warudo.wave")],
            ),
        )
        plan = _make_plan()
        result = await r.generate(plan, [])

        assert result is None

    @pytest.mark.asyncio
    async def test_replyer_empty_speech_silent(self) -> None:
        """reply tool_call 的 speech 为空 → silent 降级返回 None。"""
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(
                tool_calls=[_tool_call_reply(speech="", emotion="happy")],
            ),
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
            llm_response=_make_llm_response(tool_calls=[_tool_call_reply()]),
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
            llm_response=_make_llm_response(
                tool_calls=[_tool_call_reply(speech="这是脏话测试")],
            ),
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
            llm_response=_make_llm_response(
                tool_calls=[_tool_call_reply(speech="这是脏话测试")],
            ),
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
