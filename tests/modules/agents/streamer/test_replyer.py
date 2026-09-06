"""Replyer 单元测试（Y 模型：标准 function calling）。"""

from __future__ import annotations

import json
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.plan import DecisionPlan
from src.agents.streamer.replyer import ProfanityFilter, Replyer
from src.modules.llm.manager import LLMResponse


def _make_plan(should_reply: bool = True) -> DecisionPlan:
    return DecisionPlan(
        should_reply=should_reply,
        target="m1",
        topic_summary="打游戏",
        reply_guidance="回应夸奖，带点得意",
        confidence=0.9,
    )


def _tool_call_reply(
    speech: str = "好耶！",
    emotion: str = "happy",
    call_id: str = "call_1",
) -> dict:
    """构造一个 reply tool_call（标准 OpenAI 形态）。"""
    return {
        "name": "reply",
        "arguments": json.dumps({"speech": speech, "emotion": emotion}, ensure_ascii=False),
        "id": call_id,
        "type": "function",
    }


def _tool_call_action(
    name: str = "warudo.wave",
    parameters: Optional[dict] = None,
    call_id: str = "call_2",
) -> dict:
    """构造一个动作工具 tool_call。"""
    return {
        "name": name,
        "arguments": json.dumps(parameters or {}, ensure_ascii=False),
        "id": call_id,
        "type": "function",
    }


def _make_llm_response(
    *,
    tool_calls: Optional[List[dict]] = None,
    success: bool = True,
    error: Optional[str] = None,
) -> LLMResponse:
    """构造 LLMResponse（call_tools 返回值）。"""
    return LLMResponse(
        success=success,
        content="",
        tool_calls=tool_calls or [],
        error=error,
    )


def _make_replyer(
    llm_response: Optional[LLMResponse] = None,
    llm_side_effect: Optional[Exception] = None,
    action_tools=None,
    config: Optional[dict] = None,
    profanity_filter: Optional[ProfanityFilter] = None,
):
    """构造 Replyer + mock LLM。

    Returns:
        (replyer, llm_mock, prompt_mock)
    """
    llm = MagicMock()
    if llm_side_effect is not None:
        llm.call_tools = AsyncMock(side_effect=llm_side_effect)
    else:
        resp = llm_response or _make_llm_response()
        llm.call_tools = AsyncMock(return_value=resp)
    # 兼容旧测试可能用到的 chat 属性（不应被调用）
    llm.chat = AsyncMock()

    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="PROMPT")

    tool_registry = None
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
        profanity_filter=profanity_filter,
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
        persona = {"bot_name": "麦麦", "personality": "活泼", "style_constraints": "口语化"}

        result = await r.generate(plan, [], persona)

        assert result is not None
        assert isinstance(result, dict)
        assert result["speech"] == "好耶！"
        assert result["emotion"]["name"] == "happy"
        assert result["actions"] == []
        assert result["metadata"]["target"] == "m1"
        assert result["metadata"]["topic_summary"] == "打游戏"

    @pytest.mark.asyncio
    async def test_replyer_persona_in_prompt(self) -> None:
        """断言 prompt 渲染入参包含 $personality / $style_constraints / $bot_name。"""
        r, _llm, prompt = _make_replyer(
            llm_response=_make_llm_response(
                tool_calls=[_tool_call_reply()],
            ),
        )
        plan = _make_plan()
        persona = {
            "bot_name": "麦麦",
            "personality": "活泼开朗，有些调皮",
            "style_constraints": "口语化、简短",
        }

        await r.generate(plan, [], persona)

        kwargs = prompt.render_safe.call_args.kwargs
        assert "personality" in kwargs
        assert kwargs["personality"] == "活泼开朗，有些调皮"
        assert "style_constraints" in kwargs
        assert kwargs["style_constraints"] == "口语化、简短"
        assert "bot_name" in kwargs
        assert kwargs["bot_name"] == "麦麦"
        assert "plan" in kwargs
        assert "danmaku_batch" in kwargs
        # Y 模型：移除 $action_list（actions 走 tool_calls）
        assert "action_list" not in kwargs

    @pytest.mark.asyncio
    async def test_replyer_uses_llm_client(self) -> None:
        """断言 call_tools 使用 replyer_llm（默认 'llm'，与 Planner 的 llm_fast 分离）。"""
        r, llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(tool_calls=[_tool_call_reply()]),
        )
        plan = _make_plan()
        persona = {"bot_name": "麦麦", "personality": "p", "style_constraints": "s"}

        await r.generate(plan, [], persona)

        assert llm.call_tools.await_args.kwargs.get("client_type") == "llm"

    @pytest.mark.asyncio
    async def test_replyer_passes_reply_and_action_tools(self) -> None:
        """Y 模型：call_tools 必须传 tools（reply function + 动作工具）。"""
        from src.modules.tools.models import ToolSpec

        action_tools = [
            ToolSpec(name="warudo.wave", description="挥手", parameters_schema=None, provider="warudo"),
            ToolSpec(name="obs.switch_scene", description="切景", parameters_schema=None, provider="obs"),
        ]
        r, llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(tool_calls=[_tool_call_reply()]),
            action_tools=action_tools,
        )
        plan = _make_plan()
        persona = {"bot_name": "麦麦", "personality": "p", "style_constraints": "s"}

        await r.generate(plan, [], persona)

        kwargs = llm.call_tools.await_args.kwargs
        assert "tools" in kwargs
        tool_names = [t["name"] for t in kwargs["tools"]]
        # reply 必须在第一位
        assert tool_names[0] == "reply"
        # 动作工具从 ToolRegistry 收集
        assert "warudo.wave" in tool_names
        assert "obs.switch_scene" in tool_names

    @pytest.mark.asyncio
    async def test_replyer_filters_reply_from_action_tools(self) -> None:
        """防御：reply 不应出现在动作工具列表（即使 registry 中有同名）。"""
        from src.modules.tools.models import ToolSpec

        # 假设 ToolRegistry 中有一个"reply"工具（异常场景）——replyer 必须过滤掉
        action_tools = [
            ToolSpec(name="reply", description="some wrong reply", parameters_schema=None, provider="evil"),
            ToolSpec(name="warudo.wave", description="挥手", parameters_schema=None, provider="warudo"),
        ]
        r, llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(tool_calls=[_tool_call_reply()]),
            action_tools=action_tools,
        )
        plan = _make_plan()
        persona = {"bot_name": "麦麦", "personality": "p", "style_constraints": "s"}

        await r.generate(plan, [], persona)

        kwargs = llm.call_tools.await_args.kwargs
        tool_names = [t["name"] for t in kwargs["tools"]]
        # 只有 reply 函数定义 + warudo.wave（reply 已被过滤）
        assert tool_names.count("reply") == 1
        assert "warudo.wave" in tool_names

    @pytest.mark.asyncio
    async def test_replyer_invalid_emotion_degrades(self) -> None:
        """非法 emotion（不在 12 枚举内）→ 降级为 neutral，speech 保留。"""
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(
                tool_calls=[_tool_call_reply(speech="嗯嗯", emotion="这不是情绪")],
            ),
        )
        plan = _make_plan()
        persona = {"bot_name": "麦麦", "personality": "p", "style_constraints": "s"}

        result = await r.generate(plan, [], persona)

        assert result is not None
        assert result["emotion"]["name"] == "neutral"
        assert result["speech"] == "嗯嗯"

    @pytest.mark.asyncio
    async def test_replyer_collects_action_tool_calls(self) -> None:
        """LLM 同时返回 reply + 动作工具调用 → actions 列表正确收集。"""
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(
                tool_calls=[
                    _tool_call_reply(speech="看我的！", emotion="excited"),
                    _tool_call_action(name="warudo.wave", parameters={}),
                    _tool_call_action(
                        name="obs.switch_scene",
                        parameters={"scene_name": "main"},
                        call_id="call_3",
                    ),
                ],
            ),
        )
        plan = _make_plan()
        persona = {"bot_name": "麦麦", "personality": "p", "style_constraints": "s"}

        result = await r.generate(plan, [], persona)

        assert result is not None
        assert result["speech"] == "看我的！"
        assert result["emotion"]["name"] == "excited"
        assert len(result["actions"]) == 2
        action_names = [a["name"] for a in result["actions"]]
        assert action_names == ["warudo.wave", "obs.switch_scene"]
        # 参数正确解析（dict，不是 JSON 字符串）
        assert result["actions"][1]["parameters"] == {"scene_name": "main"}

    @pytest.mark.asyncio
    async def test_replyer_no_reply_tool_call_silent(self) -> None:
        """LLM 返回非 reply 的工具调用（无 reply call）→ silent 降级返回 None。"""
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(
                tool_calls=[_tool_call_action(name="warudo.wave")],
            ),
        )
        plan = _make_plan()
        persona = {"bot_name": "麦麦", "personality": "p", "style_constraints": "s"}

        result = await r.generate(plan, [], persona)

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
        persona = {"bot_name": "麦麦", "personality": "p", "style_constraints": "s"}

        result = await r.generate(plan, [], persona)

        assert result is None

    @pytest.mark.asyncio
    async def test_replyer_llm_failure_silent(self) -> None:
        """LLM 调用抛异常 → 返回 None（silent 降级），不抛出。"""
        r, _llm, _prompt = _make_replyer(llm_side_effect=RuntimeError("LLM 挂了"))
        plan = _make_plan()
        persona = {"bot_name": "麦麦", "personality": "p", "style_constraints": "s"}

        result = await r.generate(plan, [], persona)

        assert result is None

    @pytest.mark.asyncio
    async def test_replyer_llm_response_failure_silent(self) -> None:
        """LLMResponse.success=False → 返回 None（silent 降级）。"""
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(success=False, error="upstream error"),
        )
        plan = _make_plan()
        persona = {"bot_name": "麦麦", "personality": "p", "style_constraints": "s"}

        result = await r.generate(plan, [], persona)

        assert result is None

    @pytest.mark.asyncio
    async def test_replyer_should_reply_false_skips(self) -> None:
        """plan.should_reply=False → 不调 LLM，直接返回 None。"""
        r, llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(tool_calls=[_tool_call_reply()]),
        )
        plan = _make_plan(should_reply=False)
        persona = {"bot_name": "麦麦", "personality": "p", "style_constraints": "s"}

        result = await r.generate(plan, [], persona)

        assert result is None
        llm.call_tools.assert_not_called()


class TestReplyerProfanityFilter:
    """敏感词净化：原 output/pipelines/profanity_filter 逻辑 verbatim 归此地。"""

    def test_profanity_filter_disabled_passes_through(self) -> None:
        """filter.enabled=False 时不过滤。"""
        flt = ProfanityFilter(words=["bad"], enabled=False)
        cleaned, dropped = flt.filter("hello bad world")
        assert cleaned == "hello bad world"
        assert dropped is False

    def test_profanity_filter_replace_word(self) -> None:
        """filter.enabled=True + words=["脏话"] → 替换为 replacement。"""
        flt = ProfanityFilter(words=["脏话"], replacement="***")
        cleaned, dropped = flt.filter("这是脏话测试")
        assert cleaned == "这是***测试"
        assert dropped is True

    @pytest.mark.asyncio
    async def test_profanity_filter_drop_on_match(self) -> None:
        """drop_on_match=True 时整条返回 None。"""
        flt = ProfanityFilter(
            words=["脏话"],
            replacement="***",
            drop_on_match=True,
        )
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(
                tool_calls=[_tool_call_reply(speech="这是脏话测试")],
            ),
            profanity_filter=flt,
        )
        plan = _make_plan()
        persona = {"bot_name": "x", "personality": "p", "style_constraints": "s"}

        result = await r.generate(plan, [], persona)

        assert result is None

    @pytest.mark.asyncio
    async def test_profanity_filter_replace_keep_result(self) -> None:
        """drop_on_match=False（默认）时净化后保留 result。"""
        flt = ProfanityFilter(words=["脏话"], replacement="***")
        r, _llm, _prompt = _make_replyer(
            llm_response=_make_llm_response(
                tool_calls=[_tool_call_reply(speech="这是脏话测试")],
            ),
            profanity_filter=flt,
        )
        plan = _make_plan()
        persona = {"bot_name": "x", "personality": "p", "style_constraints": "s"}

        result = await r.generate(plan, [], persona)

        assert result is not None
        assert result["speech"] == "这是***测试"

    def test_profanity_filter_case_insensitive(self) -> None:
        """默认 case_sensitive=False 时大小写不敏感。"""
        flt = ProfanityFilter(words=["BAD"], replacement="*")
        cleaned, dropped = flt.filter("hello bad BAD BaD")
        assert dropped is True
        assert "bad" not in cleaned.lower() or "*" in cleaned
