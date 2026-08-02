"""Replyer 单元测试（双阶段决策器的 Stage 2）。

覆盖（≥7 个用例）：
- 基础生成：mock LLM → 生成 Intent
- 人设注入：prompt 渲染含 personality / style_constraints / bot_name 变量
- 客户端选择：client_type == "llm"（replyer_client，与 Planner 分离）
- 无 tools：LLM chat 调用不含 tools 参数
- 情绪降级：非法 emotion → neutral
- 动作白名单：非法 action → 丢弃 action，保留 speech
- LLM 异常静默：LLM 抛异常 → 返回 None（silent 降级，不发布事件）

人设分离承诺：Replyer 负责注入人设（Planner 零人设的另一半）。
"""

from __future__ import annotations

import json
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

# 预导入 config.schemas 以打破代码库既有的循环导入：
#   deciders/__init__ -> llm -> config.schemas.base -> config.schemas.__init__
#   -> decision_schemas -> llm_llm_decider (partially initialized)
# 与 test_plan.py / test_amaidesu_decider.py 同样的 workaround。
import src.modules.config.schemas  # noqa: F401

from src.modules.types import Intent
from src.modules.types.capabilities import UnifiedActionEntry, UnifiedCapabilitiesView
from src.stages.decision.deciders.amaidesu.plan import DecisionPlan
from src.stages.decision.deciders.amaidesu.replyer import Replyer


# ==================== 辅助工厂 ====================


def _make_plan(should_reply: bool = True) -> DecisionPlan:
    """构造一个"应回复"的标准测试计划。"""
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
    """构造一份合法的 Replyer LLM JSON 返回（字符串形式，与 QA 一致）。"""
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
    capabilities: Optional[UnifiedCapabilitiesView] = None,
    config: Optional[dict] = None,
    persona_passthrough: bool = True,
):
    """组装一个全 mock 的 Replyer 实例。

    Args:
        llm_return: LLM chat 正常返回值（字符串 JSON）。与 llm_side_effect 互斥。
        llm_side_effect: LLM chat 抛出的异常（用于异常测试）。
        capabilities: 能力视图（None 表示不注入能力提供者）。
        config: 覆盖默认配置 dict。
        persona_passthrough: 是否保留 prompt_service mock 引用以便断言渲染入参。
    """
    llm = MagicMock()
    if llm_side_effect is not None:
        llm.chat = AsyncMock(side_effect=llm_side_effect)
    else:
        llm.chat = AsyncMock(return_value=llm_return)

    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="PROMPT")

    eb = MagicMock()
    eb.emit = AsyncMock()

    cap_provider = None
    if capabilities is not None:
        cap_provider = MagicMock()
        cap_provider.get_all_capabilities = MagicMock(return_value=capabilities)

    cfg = {"replyer_client": "llm"}
    if config:
        cfg.update(config)

    r = Replyer(
        config=cfg,
        llm_service=llm,
        prompt_service=prompt,
        event_bus=eb,
        capabilities_provider=cap_provider,
    )
    return r, llm, prompt, eb


# ==================== 测试用例 ====================


class TestReplyerGenerate:
    """基础生成行为。"""

    @pytest.mark.asyncio
    async def test_replyer_generates_intent(self) -> None:
        """mock LLM 返回合法 JSON → 生成包含 speech/emotion/action 的 Intent。"""
        r, llm, _prompt, _eb = _make_replyer(llm_return=_ok_payload(text="好耶！", emotion="happy"))
        plan = _make_plan()
        persona = {"bot_name": "爱德丝", "personality": "活泼", "style_constraints": "口语化"}

        intent = await r.generate(plan, [], persona)

        assert intent is not None
        assert isinstance(intent, Intent)
        assert intent.speech == "好耶！"
        assert intent.emotion is not None
        assert intent.emotion.name == "happy"
        assert intent.action is not None
        assert intent.action.name == "warudo.wave"

    @pytest.mark.asyncio
    async def test_replyer_persona_in_prompt(self) -> None:
        """断言 prompt 渲染入参包含 $personality / $style_constraints / $bot_name（人设分离承诺）。"""
        r, _llm, prompt, _eb = _make_replyer(llm_return=_ok_payload())
        plan = _make_plan()
        persona = {
            "bot_name": "爱德丝",
            "personality": "活泼开朗，有些调皮",
            "style_constraints": "口语化、简短",
        }

        await r.generate(plan, [], persona)

        kwargs = prompt.render_safe.call_args.kwargs
        # 三个人设变量都必须注入
        assert "personality" in kwargs
        assert kwargs["personality"] == "活泼开朗，有些调皮"
        assert "style_constraints" in kwargs
        assert kwargs["style_constraints"] == "口语化、简短"
        assert "bot_name" in kwargs
        assert kwargs["bot_name"] == "爱德丝"
        # 计划与弹幕上下文也要传入
        assert "plan" in kwargs
        assert "danmaku_batch" in kwargs

    @pytest.mark.asyncio
    async def test_replyer_uses_llm_client(self) -> None:
        """断言 chat 调用使用 replyer_client（默认 'llm'，与 Planner 的 llm_fast 分离）。"""
        r, llm, _prompt, _eb = _make_replyer(llm_return=_ok_payload())
        plan = _make_plan()
        persona = {"bot_name": "爱德丝", "personality": "p", "style_constraints": "s"}

        await r.generate(plan, [], persona)

        assert llm.chat.await_args.kwargs.get("client_type") == "llm"

    @pytest.mark.asyncio
    async def test_replyer_no_tools(self) -> None:
        """断言 LLM 调用不含 tools 参数（Replyer 不做 function calling）。"""
        r, llm, _prompt, _eb = _make_replyer(llm_return=_ok_payload())
        plan = _make_plan()
        persona = {"bot_name": "爱德丝", "personality": "p", "style_constraints": "s"}

        await r.generate(plan, [], persona)

        kwargs = llm.chat.await_args.kwargs
        assert "tools" not in kwargs, f"Replyer chat 不应传 tools，实际 kwargs={kwargs}"

    @pytest.mark.asyncio
    async def test_replyer_invalid_emotion_degrades(self) -> None:
        """非法 emotion（不在 12 枚举内）→ 降级为 neutral，speech 保留。"""
        r, _llm, _prompt, _eb = _make_replyer(llm_return=_ok_payload(text="嗯嗯", emotion="这不是情绪"))
        plan = _make_plan()
        persona = {"bot_name": "爱德丝", "personality": "p", "style_constraints": "s"}

        intent = await r.generate(plan, [], persona)

        assert intent is not None
        assert intent.emotion is not None
        assert intent.emotion.name == "neutral"
        assert intent.speech == "嗯嗯"

    @pytest.mark.asyncio
    async def test_replyer_invalid_action_dropped(self) -> None:
        """注入能力白名单后，非法 action → action 丢弃为 None，speech 保留。"""
        view = UnifiedCapabilitiesView(actions=[UnifiedActionEntry(name="warudo.wave")])
        r, _llm, _prompt, _eb = _make_replyer(
            llm_return=_ok_payload(text="哈哈", action="invalid.dance"),
            capabilities=view,
        )
        plan = _make_plan()
        persona = {"bot_name": "爱德丝", "personality": "p", "style_constraints": "s"}

        intent = await r.generate(plan, [], persona)

        assert intent is not None
        assert intent.speech == "哈哈"
        # 非法动作被丢弃
        assert intent.action is None

    @pytest.mark.asyncio
    async def test_replyer_llm_failure_silent(self) -> None:
        """LLM 调用抛异常 → 返回 None（silent 降级），不抛出、不发布事件。"""
        r, llm, _prompt, eb = _make_replyer(llm_side_effect=RuntimeError("LLM 挂了"))
        plan = _make_plan()
        persona = {"bot_name": "爱德丝", "personality": "p", "style_constraints": "s"}

        result = await r.generate(plan, [], persona)

        assert result is None
        # Replyer.generate 不负责发布事件（Decider 在 Task 10 负责），故 emit 不应被调用
        eb.emit.assert_not_awaited()


class TestReplyerConfigClient:
    """配置驱动的 replyer_client 字段。"""

    @pytest.mark.asyncio
    async def test_replyer_client_override(self) -> None:
        """显式配置 replyer_client 时，chat 透传该 client_type。"""
        r, llm, _prompt, _eb = _make_replyer(
            llm_return=_ok_payload(),
            config={"replyer_client": "vlm"},
        )
        plan = _make_plan()
        persona = {"bot_name": "x", "personality": "p", "style_constraints": "s"}

        await r.generate(plan, [], persona)

        assert llm.chat.await_args.kwargs.get("client_type") == "vlm"
