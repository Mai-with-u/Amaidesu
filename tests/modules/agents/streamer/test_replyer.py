"""Replyer 单元测试（Wave 6 Streamer Agent Stage 2 表达引擎）。"""

from __future__ import annotations

import json
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.plan import DecisionPlan
from src.agents.streamer.replyer import ProfanityFilter, Replyer


def _make_plan(should_reply: bool = True) -> DecisionPlan:
    return DecisionPlan(
        should_reply=should_reply,
        target="m1",
        topic_summary="打游戏",
        reply_guidance="回应夸奖，带点得意",
        confidence=0.9,
    )


def _ok_payload(
    text: str = "好耶！",
    emotion: str = "happy",
    action: str = "warudo.wave",
    action_parameters: Optional[dict] = None,
) -> str:
    return json.dumps(
        {
            "text": text,
            "emotion": emotion,
            "action": action,
            "action_parameters": action_parameters or {},
        },
        ensure_ascii=False,
    )


def _make_replyer(
    llm_return: Optional[str] = None,
    llm_side_effect: Optional[Exception] = None,
    capabilities=None,
    config: Optional[dict] = None,
    profanity_filter: Optional[ProfanityFilter] = None,
):
    llm = MagicMock()
    if llm_side_effect is not None:
        llm.chat = AsyncMock(side_effect=llm_side_effect)
    else:
        llm.chat = AsyncMock(return_value=llm_return)

    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="PROMPT")

    cap_provider = None
    if capabilities is not None:
        cap_provider = MagicMock()
        from src.modules.types.capabilities import UnifiedCapabilitiesView
        cap_provider.get_all_capabilities = MagicMock(return_value=capabilities)

    cfg = {"replyer_llm": "llm"}
    if config:
        cfg.update(config)

    r = Replyer(
        config=cfg,
        llm_service=llm,
        prompt_service=prompt,
        capabilities_provider=cap_provider,
        profanity_filter=profanity_filter,
    )
    return r, llm, prompt


class TestReplyerGenerate:
    """基础生成行为。"""

    @pytest.mark.asyncio
    async def test_replyer_generates_result(self) -> None:
        """mock LLM 返回合法 JSON → 生成包含 speech/emotion/action 的 dict。"""
        r, _llm, _prompt = _make_replyer(llm_return=_ok_payload(text="好耶！", emotion="happy"))
        plan = _make_plan()
        persona = {"bot_name": "麦麦", "personality": "活泼", "style_constraints": "口语化"}

        result = await r.generate(plan, [], persona)

        assert result is not None
        assert isinstance(result, dict)
        assert result["speech"] == "好耶！"
        assert result["emotion"]["name"] == "happy"
        action = result["action"]
        assert action is not None
        assert action["name"] == "warudo.wave"

    @pytest.mark.asyncio
    async def test_replyer_persona_in_prompt(self) -> None:
        """断言 prompt 渲染入参包含 $personality / $style_constraints / $bot_name。"""
        r, _llm, prompt = _make_replyer(llm_return=_ok_payload())
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

    @pytest.mark.asyncio
    async def test_replyer_uses_llm_client(self) -> None:
        """断言 chat 调用使用 replyer_llm（默认 'llm'，与 Planner 的 llm_fast 分离）。"""
        r, llm, _prompt = _make_replyer(llm_return=_ok_payload())
        plan = _make_plan()
        persona = {"bot_name": "麦麦", "personality": "p", "style_constraints": "s"}

        await r.generate(plan, [], persona)

        assert llm.chat.await_args.kwargs.get("client_type") == "llm"

    @pytest.mark.asyncio
    async def test_replyer_no_tools(self) -> None:
        """断言 LLM 调用不含 tools 参数（Replyer 不做 function calling）。"""
        r, llm, _prompt = _make_replyer(llm_return=_ok_payload())
        plan = _make_plan()
        persona = {"bot_name": "麦麦", "personality": "p", "style_constraints": "s"}

        await r.generate(plan, [], persona)

        kwargs = llm.chat.await_args.kwargs
        assert "tools" not in kwargs, f"Replyer chat 不应传 tools，实际 kwargs={kwargs}"

    @pytest.mark.asyncio
    async def test_replyer_invalid_emotion_degrades(self) -> None:
        """非法 emotion（不在 12 枚举内）→ 降级为 neutral，speech 保留。"""
        r, _llm, _prompt = _make_replyer(llm_return=_ok_payload(text="嗯嗯", emotion="这不是情绪"))
        plan = _make_plan()
        persona = {"bot_name": "麦麦", "personality": "p", "style_constraints": "s"}

        result = await r.generate(plan, [], persona)

        assert result is not None
        assert result["emotion"]["name"] == "neutral"
        assert result["speech"] == "嗯嗯"

    @pytest.mark.asyncio
    async def test_replyer_invalid_action_dropped(self) -> None:
        """注入能力白名单后，非法 action → action 丢弃为 None，speech 保留。"""
        from src.modules.types.capabilities import UnifiedActionEntry, UnifiedCapabilitiesView

        view = UnifiedCapabilitiesView(actions=[UnifiedActionEntry(name="warudo.wave")])
        r, _llm, _prompt = _make_replyer(
            llm_return=_ok_payload(text="哈哈", action="invalid.dance"),
            capabilities=view,
        )
        plan = _make_plan()
        persona = {"bot_name": "麦麦", "personality": "p", "style_constraints": "s"}

        result = await r.generate(plan, [], persona)

        assert result is not None
        assert result["speech"] == "哈哈"
        assert result["action"] is None

    @pytest.mark.asyncio
    async def test_replyer_llm_failure_silent(self) -> None:
        """LLM 调用抛异常 → 返回 None（silent 降级），不抛出。"""
        r, llm, _prompt = _make_replyer(llm_side_effect=RuntimeError("LLM 挂了"))
        plan = _make_plan()
        persona = {"bot_name": "麦麦", "personality": "p", "style_constraints": "s"}

        result = await r.generate(plan, [], persona)

        assert result is None


class TestReplyerProfanityFilter:
    """§1.46.1 敏感词净化：原 output/pipelines/profanity_filter 逻辑 verbatim 归此地。"""

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
            llm_return=_ok_payload(text="这是脏话测试"),
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
            llm_return=_ok_payload(text="这是脏话测试"),
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