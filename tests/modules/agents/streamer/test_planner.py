"""Planner 单元测试（Wave 6 Streamer Agent Stage 1 决策核心）。

迁移自 ``tests/stages/decision/deciders/amaidesu/test_planner.py``，保持人设隔离 /
零工具调用 / 客户端选择 / JSON 解析等核心契约。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.plan import DecisionPlan
from src.agents.streamer.planner import Planner


def _make_llm_response(content: str, *, success: bool = True) -> MagicMock:
    """构造模拟的 LLMResponse（鸭子类型：含 .success / .content 字段）。"""
    m = MagicMock()
    m.success = success
    m.content = content
    m.error = None if success else "mock error"
    return m


def _make_planner(
    *,
    planner_llm: str = "llm_fast",
    llm_return: str | None = None,
    llm_raises: Exception | None = None,
) -> tuple[Planner, MagicMock, MagicMock, MagicMock]:
    """构造一个注入 mock 依赖的 Planner。"""
    llm = MagicMock()
    if llm_raises is not None:
        llm.chat = AsyncMock(side_effect=llm_raises)
    else:
        llm.chat = AsyncMock(return_value=llm_return)

    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="PROMPT")

    rs = MagicMock()
    rs.get_snapshot = MagicMock(return_value=MagicMock(heat="low", topics=[], sc_queue=[]))

    planner = Planner(
        config={"planner_llm": planner_llm},
        llm_service=llm,
        prompt_service=prompt,
        room_state=rs,
    )
    return planner, llm, prompt, rs


class TestPlannerHappyPath:
    """正常路径测试。"""

    @pytest.mark.asyncio
    async def test_plan_forced_returns_plan(self) -> None:
        """mock LLM 返回 should_reply=true → 返回非空 DecisionPlan。"""
        raw = json.dumps(
            {
                "should_reply": True,
                "target": "m1",
                "topic_summary": "打游戏",
                "reply_guidance": "回应",
                "confidence": 0.9,
            }
        )
        planner, llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        plan = await planner.plan([], forced=True)

        assert plan is not None
        assert isinstance(plan, DecisionPlan)
        assert plan.should_reply is True
        assert plan.target == "m1"
        assert plan.topic_summary == "打游戏"
        assert plan.reply_guidance == "回应"
        assert plan.confidence == pytest.approx(0.9)


class TestPlannerPersonaIsolation:
    """人设分离承诺：Planner prompt 零人设变量。"""

    @pytest.mark.asyncio
    async def test_plan_no_persona_in_prompt(self) -> None:
        """render_safe 调用 kwargs 中不得包含任何 persona 变量。"""
        raw = json.dumps({"should_reply": False})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        await planner.plan([], forced=False)

        kwargs = prompt.render_safe.call_args.kwargs
        assert "personality" not in kwargs, "Planner prompt 不得注入 personality"
        assert "style_constraints" not in kwargs, "Planner prompt 不得注入 style_constraints"
        assert "bot_name" not in kwargs, "Planner prompt 不得注入 bot_name"


class TestPlannerClientType:
    """client_type 选择测试。"""

    @pytest.mark.asyncio
    async def test_plan_uses_llm_fast(self) -> None:
        """默认 planner_llm=llm_fast，且透传到 chat 调用。"""
        raw = json.dumps({"should_reply": True})
        planner, llm, _prompt, _rs = _make_planner(
            planner_llm="llm_fast",
            llm_return=_make_llm_response(raw),
        )

        await planner.plan([], forced=True)

        assert llm.chat.await_args.kwargs.get("client_type") == "llm_fast"

    @pytest.mark.asyncio
    async def test_plan_uses_configured_client(self) -> None:
        """配置成 llm 时应使用 llm（证明不硬编码）。"""
        raw = json.dumps({"should_reply": True})
        planner, llm, _prompt, _rs = _make_planner(
            planner_llm="llm",
            llm_return=_make_llm_response(raw),
        )

        await planner.plan([], forced=True)

        assert llm.chat.await_args.kwargs.get("client_type") == "llm"


class TestPlannerNoTools:
    """Planner 不做工具调用。"""

    @pytest.mark.asyncio
    async def test_plan_no_tools(self) -> None:
        """chat 调用 kwargs 中不得包含 tools 参数。"""
        raw = json.dumps({"should_reply": True})
        planner, llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        await planner.plan([], forced=True)

        kwargs = llm.chat.await_args.kwargs
        assert "tools" not in kwargs, "Planner chat 调用不得传 tools 参数"
        args = llm.chat.await_args.args
        assert len(args) <= 1, "chat 只允许 prompt 位置参数"


class TestPlannerFailurePaths:
    """降级路径测试。"""

    @pytest.mark.asyncio
    async def test_plan_llm_failure_returns_none(self) -> None:
        """LLM 调用抛异常 → 返回 None（由调用方降级处理）。"""
        planner, _llm, _prompt, _rs = _make_planner(llm_raises=RuntimeError("LLM 挂了"))

        plan = await planner.plan([], forced=True)

        assert plan is None

    @pytest.mark.asyncio
    async def test_plan_llm_unsuccessful_returns_none(self) -> None:
        """LLM 返回 success=False → 返回 None。"""
        bad = _make_llm_response("", success=False)
        planner, _llm, _prompt, _rs = _make_planner(llm_return=bad)

        plan = await planner.plan([], forced=True)

        assert plan is None


class TestPlannerJsonParsing:
    """JSON 解析测试。"""

    @pytest.mark.asyncio
    async def test_plan_invalid_json_returns_none(self) -> None:
        """脏 JSON → 解析失败 → 返回 None。"""
        planner, _llm, _prompt, _rs = _make_planner(
            llm_return=_make_llm_response("这不是 JSON，是主播的胡言乱语")
        )

        plan = await planner.plan([], forced=True)

        assert plan is None

    @pytest.mark.asyncio
    async def test_plan_json_with_codeblock_stripped(self) -> None:
        """带 ```json 包裹的合法 JSON 也能被清理后解析（_clean_llm_json 作用）。"""
        raw = '```json\n{"should_reply": true, "target": "all"}\n```'
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        plan = await planner.plan([], forced=True)

        assert plan is not None
        assert plan.should_reply is True
        assert plan.target == "all"

    @pytest.mark.asyncio
    async def test_plan_trailing_comma_tolerated(self) -> None:
        """LLM 常见的尾随逗号错误应被 _clean_llm_json 修复。"""
        raw = '{"should_reply": true, "target": "x", }'
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        plan = await planner.plan([], forced=True)

        assert plan is not None
        assert plan.should_reply is True


class TestPlannerConfidenceGate:
    """``should_reply=true`` 但 ``confidence`` 过低 → 降级静默（P0）。"""

    @pytest.mark.asyncio
    async def test_low_confidence_non_forced_silenced(self) -> None:
        raw = json.dumps(
            {
                "should_reply": True,
                "target": "all",
                "topic_summary": "没想好聊什么",
                "reply_guidance": "硬聊",
                "confidence": 0.0,
            }
        )
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        plan = await planner.plan([], forced=False)

        assert plan is not None
        assert plan.should_reply is False, "低置信度 + 非 forced 应降级静默"
        assert plan.topic_summary == ""
        assert plan.reply_guidance == ""

    @pytest.mark.asyncio
    async def test_low_confidence_forced_keeps_reply(self) -> None:
        raw = json.dumps({"should_reply": True, "target": "sc_user", "confidence": 0.0})
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        plan = await planner.plan([], forced=True)

        assert plan is not None
        assert plan.should_reply is True, "forced 场景不应降级"
        assert plan.target == "sc_user"

    @pytest.mark.asyncio
    async def test_confidence_at_threshold_keeps_reply(self) -> None:
        raw = json.dumps({"should_reply": True, "confidence": 0.3})
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        plan = await planner.plan([], forced=False)

        assert plan is not None
        assert plan.should_reply is True, "confidence=0.3 恰好等于阈值不应降级"


class TestPlannerForcedFlag:
    """forced 标志传播测试。"""

    @pytest.mark.asyncio
    async def test_plan_forced_flag_passed_to_prompt(self) -> None:
        """forced 标志的 True/False 值应区分传递到 prompt 变量。"""
        raw_t = json.dumps({"should_reply": True})
        planner_t, _llm_t, prompt_t, _rs_t = _make_planner(llm_return=_make_llm_response(raw_t))
        await planner_t.plan([], forced=True)
        kwargs_t = prompt_t.render_safe.call_args.kwargs
        assert "forced" in kwargs_t, "render_safe 必须接收 forced 变量"
        forced_true = kwargs_t["forced"]
        assert forced_true in (True, "true"), f"forced=True 时 prompt 变量应为 True 或 'true'，实际: {forced_true!r}"

        raw_f = json.dumps({"should_reply": False})
        planner_f, _llm_f, prompt_f, _rs_f = _make_planner(llm_return=_make_llm_response(raw_f))
        await planner_f.plan([], forced=False)
        kwargs_f = prompt_f.render_safe.call_args.kwargs
        assert "forced" in kwargs_f
        forced_false = kwargs_f["forced"]
        assert forced_false in (False, "false"), (
            f"forced=False 时 prompt 变量应为 False 或 'false'，实际: {forced_false!r}"
        )
        assert forced_true != forced_false, (
            f"forced=True/False 的 prompt 变量必须不同，实际均为 {forced_true!r}"
        )


class TestPlannerAgendaContext:
    """Agenda 上下文注入测试（Wave 6：agenda 替代 outline）。"""

    @pytest.mark.asyncio
    async def test_plan_agenda_text_passed_to_prompt(self) -> None:
        """agenda_text 非 None → 透传到 prompt 的 $agenda 变量。"""
        raw = json.dumps({"should_reply": False})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        agenda_text = "当前环节：开场（1/3）\n任务：自我介绍"
        await planner.plan([], forced=False, agenda_text=agenda_text)

        kwargs = prompt.render_safe.call_args.kwargs
        assert kwargs.get("agenda") == agenda_text

    @pytest.mark.asyncio
    async def test_plan_no_agenda_text_uses_placeholder(self) -> None:
        """agenda_text=None/空 → 使用占位文本。"""
        raw = json.dumps({"should_reply": False})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        await planner.plan([], forced=False, agenda_text=None)

        kwargs = prompt.render_safe.call_args.kwargs
        assert kwargs.get("agenda") == "（当前无节目单）"