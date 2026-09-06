"""Planner 单元测试（Wave 6 Streamer Agent Stage 1 决策核心）。

迁移自 ``tests/stages/decision/deciders/amaidesu/test_planner.py``，保持人设隔离 /
function calling 结构化输出 / 客户端选择 / tool_calls 解析等核心契约。

v2.5：Planner LLM 调用改为 ``call_tools(tools=[produce_plan_fn_def])``，
所有 LLM 响应 mock 形态对齐 ``LLMResponse(success, content, tool_calls)``。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.plan import DecisionPlan
from src.agents.streamer.planner import Planner, PRODUCE_PLAN_FN_DEF
from src.modules.llm.manager import LLMResponse


def _make_llm_response(
    plan_args: Optional[Dict[str, Any]],
    *,
    success: bool = True,
    tool_calls_override: Optional[List[Dict[str, Any]]] = None,
) -> LLMResponse:
    """构造模拟的 LLMResponse（function calling 形态：tool_calls 包 produce_plan）。

    Args:
        plan_args: produce_plan 的参数 dict（会被 json.dumps 注入 arguments 字符串）。
            ``None`` 时返回空 tool_calls（模拟 LLM 未调用任何工具）。
        success: 调用成功标志；``False`` 时 Planner 视为失败降级。
        tool_calls_override: 自定义 tool_calls 列表（用于测试"调用了别的工具"等异常路径）。
    """
    if not success:
        return LLMResponse(success=False, content=None, error="mock error")
    if tool_calls_override is not None:
        return LLMResponse(success=True, content="", tool_calls=tool_calls_override)
    if plan_args is None:
        return LLMResponse(success=True, content="", tool_calls=[])
    return LLMResponse(
        success=True,
        content="",
        tool_calls=[
            {
                "name": "produce_plan",
                "arguments": json.dumps(plan_args, ensure_ascii=False),
            }
        ],
    )


def _make_planner(
    *,
    planner_llm: str = "llm_fast",
    llm_return: Any = None,
    llm_raises: Optional[Exception] = None,
) -> tuple[Planner, MagicMock, MagicMock, MagicMock]:
    """构造一个注入 mock 依赖的 Planner（mock call_tools 而非 chat）。"""
    llm = MagicMock()
    if llm_raises is not None:
        llm.call_tools = AsyncMock(side_effect=llm_raises)
    else:
        llm.call_tools = AsyncMock(return_value=llm_return)

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
        plan_args = {
            "should_reply": True,
            "target": "m1",
            "topic_summary": "打游戏",
            "reply_guidance": "回应",
            "confidence": 0.9,
        }
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(plan_args))

        plan = await planner.plan([], forced=True)

        assert plan is not None
        assert isinstance(plan, DecisionPlan)
        assert plan.should_reply is True
        assert plan.target == "m1"
        assert plan.topic_summary == "打游戏"
        assert plan.reply_guidance == "回应"
        assert plan.confidence == pytest.approx(0.9)


class TestPlannerPersonaIsolation:
    """人设分离承诺（v2.0.6 B2 反转后）：

    Planner prompt 注入 ``behavior_style``（行动准则 → 决策侧），
    但仍**不**注入 ``personality`` / ``style_constraints`` / ``bot_name``
    （身份与表达层 → 仅进 Replyer 表达侧）。
    """

    @pytest.mark.asyncio
    async def test_plan_behavior_style_injected_others_excluded(self) -> None:
        """render_safe kwargs 必须含 $behavior_style；不含 $personality/$style_constraints/$bot_name。

        反转自 v2.0.5 的"零人设"契约：现在注入行动准则（behavior_style），
        但身份/表达人设仍严格隔离（不进 Planner 表达侧）。MaiBot 三层人格拆分
        在 Amaidesu 的映射：personality+style_constraints=表达侧（Replyer），
        behavior_style=决策侧（Planner）。
        """
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response({"should_reply": False}))

        await planner.plan([], forced=False)

        kwargs = prompt.render_safe.call_args.kwargs
        # 决策侧：必须注入 behavior_style
        assert "behavior_style" in kwargs, "Planner prompt 必须注入 $behavior_style（行动准则）"
        # 身份/表达侧：仍必须隔离
        assert "personality" not in kwargs, "Planner prompt 不得注入 personality（仅 Replyer 消费）"
        assert "style_constraints" not in kwargs, "Planner prompt 不得注入 style_constraints（仅 Replyer 消费）"
        assert "bot_name" not in kwargs, "Planner prompt 不得注入 bot_name（仅 Replyer 消费）"

    @pytest.mark.asyncio
    async def test_plan_behavior_style_default_placeholder_when_empty(self) -> None:
        """behavior_style 未注入时（空串），Planner 应渲染占位文本而非字面 $behavior_style。"""
        # _make_planner 不传 behavior_style → Planner 内部 _behavior_style=""
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response({"should_reply": False}))

        await planner.plan([], forced=False)

        kwargs = prompt.render_safe.call_args.kwargs
        rendered = kwargs.get("behavior_style")
        assert isinstance(rendered, str), "behavior_style 渲染值必须是字符串"
        # 占位文本包含"未配置"标识，避免字面 ``$behavior_style`` 漏到 prompt
        assert "未配置" in rendered or "行动准则" in rendered, f"behavior_style 空时应渲染占位文本，实际: {rendered!r}"

    @pytest.mark.asyncio
    async def test_plan_behavior_style_propagates_when_set(self) -> None:
        """显式传入 behavior_style 时，渲染值必须如实透传（不做过滤/截断）。"""
        # 直接构造 Planner 注入 behavior_style
        llm = MagicMock()
        llm.call_tools = AsyncMock(return_value=_make_llm_response({"should_reply": False}))
        prompt = MagicMock()
        prompt.render_safe = MagicMock(return_value="PROMPT")
        rs = MagicMock()
        rs.get_snapshot = MagicMock(return_value=MagicMock(heat="low", topics=[], sc_queue=[]))

        custom_style = "积极与观众互动，收到礼物和SC及时致谢"
        planner = Planner(
            config={"planner_llm": "llm_fast"},
            llm_service=llm,
            prompt_service=prompt,
            room_state=rs,
            behavior_style=custom_style,
        )
        await planner.plan([], forced=True)

        kwargs = prompt.render_safe.call_args.kwargs
        assert kwargs.get("behavior_style") == custom_style, (
            f"behavior_style 渲染值应等于透传值，实际: {kwargs.get('behavior_style')!r}"
        )


class TestPlannerClientType:
    """client_type 选择测试。"""

    @pytest.mark.asyncio
    async def test_plan_uses_llm_fast(self) -> None:
        """默认 planner_llm=llm_fast，且透传到 call_tools 调用。"""
        planner, llm, _prompt, _rs = _make_planner(
            planner_llm="llm_fast",
            llm_return=_make_llm_response({"should_reply": True}),
        )

        await planner.plan([], forced=True)

        assert llm.call_tools.await_args.kwargs.get("client_type") == "llm_fast"

    @pytest.mark.asyncio
    async def test_plan_uses_configured_client(self) -> None:
        """配置成 llm 时应使用 llm（证明不硬编码）。"""
        planner, llm, _prompt, _rs = _make_planner(
            planner_llm="llm",
            llm_return=_make_llm_response({"should_reply": True}),
        )

        await planner.plan([], forced=True)

        assert llm.call_tools.await_args.kwargs.get("client_type") == "llm"


class TestPlannerUsesProducePlanTool:
    """Planner 通过 produce_plan function calling 产出决策（Y 模型标准接口）。"""

    @pytest.mark.asyncio
    async def test_plan_calls_produce_plan_fn(self) -> None:
        """call_tools 调用必须传 produce_plan function def（单元素工具列表）。"""
        planner, llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response({"should_reply": True}))

        await planner.plan([], forced=True)

        kwargs = llm.call_tools.await_args.kwargs
        assert "tools" in kwargs, "call_tools 调用必须传 tools 参数"
        tools = kwargs["tools"]
        assert isinstance(tools, list) and len(tools) == 1, "Planner 只声明 produce_plan 一个工具"
        assert tools[0].get("name") == "produce_plan", "工具函数名必须是 produce_plan"

    @pytest.mark.asyncio
    async def test_plan_passes_planner_llm_constant_alignment(self) -> None:
        """运行时声明的 produce_plan fn def 与模块常量 ``PRODUCE_PLAN_FN_DEF`` 名称一致。"""
        planner, llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response({"should_reply": True}))

        await planner.plan([], forced=True)

        tools = llm.call_tools.await_args.kwargs["tools"]
        assert tools[0]["name"] == PRODUCE_PLAN_FN_DEF["name"], "运行时 fn def 必须与 PRODUCE_PLAN_FN_DEF 对齐"


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
        bad = _make_llm_response(None, success=False)
        planner, _llm, _prompt, _rs = _make_planner(llm_return=bad)

        plan = await planner.plan([], forced=True)

        assert plan is None


class TestPlannerToolCallsParsing:
    """tool_calls 解析路径测试（v2.5 替代原 JSON 解析测试）。"""

    @pytest.mark.asyncio
    async def test_plan_empty_tool_calls_returns_none(self) -> None:
        """LLM 没调用任何工具（tool_calls=[]）→ 决策结构缺失 → 返回 None。"""
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(None))

        plan = await planner.plan([], forced=True)

        assert plan is None
        assert planner.last_failure == "llm_no_tool_calls"

    @pytest.mark.asyncio
    async def test_plan_missing_produce_plan_tool_call_returns_none(self) -> None:
        """LLM 调用了别的工具但没调 produce_plan → 决策结构缺失 → 返回 None。"""
        bad_calls = [{"name": "other_tool", "arguments": "{}"}]
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(None, tool_calls_override=bad_calls))

        plan = await planner.plan([], forced=True)

        assert plan is None
        assert planner.last_failure == "produce_plan_not_called"

    @pytest.mark.asyncio
    async def test_plan_invalid_arguments_json_returns_none(self) -> None:
        """produce_plan.arguments 字符串不是合法 JSON → 返回 None。"""
        bad_calls = [{"name": "produce_plan", "arguments": "这不是 JSON，是主播的胡言乱语"}]
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(None, tool_calls_override=bad_calls))

        plan = await planner.plan([], forced=True)

        assert plan is None
        assert planner.last_failure is not None
        assert "json_parse_failed" in planner.last_failure


class TestPlannerConfidenceGate:
    """``should_reply=true`` 但 ``confidence`` 过低 → 降级静默（P0）。"""

    @pytest.mark.asyncio
    async def test_low_confidence_non_forced_silenced(self) -> None:
        plan_args = {
            "should_reply": True,
            "target": "all",
            "topic_summary": "没想好聊什么",
            "reply_guidance": "硬聊",
            "confidence": 0.0,
        }
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(plan_args))

        plan = await planner.plan([], forced=False)

        assert plan is not None
        assert plan.should_reply is False, "低置信度 + 非 forced 应降级静默"
        assert plan.topic_summary == ""
        assert plan.reply_guidance == ""

    @pytest.mark.asyncio
    async def test_low_confidence_forced_keeps_reply(self) -> None:
        plan_args = {"should_reply": True, "target": "sc_user", "confidence": 0.0}
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(plan_args))

        plan = await planner.plan([], forced=True)

        assert plan is not None
        assert plan.should_reply is True, "forced 场景不应降级"
        assert plan.target == "sc_user"

    @pytest.mark.asyncio
    async def test_confidence_at_threshold_keeps_reply(self) -> None:
        plan_args = {"should_reply": True, "confidence": 0.3}
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(plan_args))

        plan = await planner.plan([], forced=False)

        assert plan is not None
        assert plan.should_reply is True, "confidence=0.3 恰好等于阈值不应降级"


class TestPlannerForcedFlag:
    """forced 标志传播测试。"""

    @pytest.mark.asyncio
    async def test_plan_forced_flag_passed_to_prompt(self) -> None:
        """forced 标志的 True/False 值应区分传递到 prompt 变量。"""
        planner_t, _llm_t, prompt_t, _rs_t = _make_planner(llm_return=_make_llm_response({"should_reply": True}))
        await planner_t.plan([], forced=True)
        kwargs_t = prompt_t.render_safe.call_args.kwargs
        assert "forced" in kwargs_t, "render_safe 必须接收 forced 变量"
        forced_true = kwargs_t["forced"]
        assert forced_true in (True, "true"), f"forced=True 时 prompt 变量应为 True 或 'true'，实际: {forced_true!r}"

        planner_f, _llm_f, prompt_f, _rs_f = _make_planner(llm_return=_make_llm_response({"should_reply": False}))
        await planner_f.plan([], forced=False)
        kwargs_f = prompt_f.render_safe.call_args.kwargs
        assert "forced" in kwargs_f
        forced_false = kwargs_f["forced"]
        assert forced_false in (False, "false"), (
            f"forced=False 时 prompt 变量应为 False 或 'false'，实际: {forced_false!r}"
        )
        assert forced_true != forced_false, f"forced=True/False 的 prompt 变量必须不同，实际均为 {forced_true!r}"


class TestPlannerAgendaContext:
    """Agenda 上下文注入测试（v2.2：agenda_text 进入 PlannerAssembler 的
    stage_descriptions 字段，最终落地在 ``$context_block`` 的"环节描述"段）。"""

    @pytest.mark.asyncio
    async def test_plan_agenda_text_passed_to_prompt(self) -> None:
        """agenda_text 非空 → 透传到 prompt 的 $context_block 内"环节描述"段。"""
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response({"should_reply": False}))

        agenda_text = "当前环节：开场（1/3）\n任务：自我介绍"
        await planner.plan([], forced=False, agenda_text=agenda_text)

        kwargs = prompt.render_safe.call_args.kwargs
        assert "context_block" in kwargs
        assert agenda_text in kwargs["context_block"]

    @pytest.mark.asyncio
    async def test_plan_no_agenda_text_uses_placeholder(self) -> None:
        """agenda_text=None/空 → 环节描述段使用 "（无）" 占位。"""
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response({"should_reply": False}))

        await planner.plan([], forced=False, agenda_text=None)

        kwargs = prompt.render_safe.call_args.kwargs
        assert "context_block" in kwargs
        # PlannerAssembler 默认 placeholder
        assert "## 环节描述\n（无）" in kwargs["context_block"]


class TestPlannerContextBlockStructure:
    """PlannerAssembler 8 段结构透出到 context_block 的契约测试。"""

    @pytest.mark.asyncio
    async def test_context_block_has_eight_sections(self) -> None:
        """context_block 必须包含 PlannerAssembler 的全部 8 段标题。"""
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response({"should_reply": False}))

        await planner.plan([], forced=True)

        kwargs = prompt.render_safe.call_args.kwargs
        block = kwargs["context_block"]
        for title in (
            "系统人格",
            "可用工具",
            "环节描述",
            "时间线摘要",
            "直播流（窗口）",
            "直播间快照",
            "工作记忆",
            "记忆召回",
        ):
            assert f"## {title}" in block, f"context_block 缺少 8 段之：{title}"

    @pytest.mark.asyncio
    async def test_context_block_contains_danmaku_batch(self) -> None:
        """弹幕批次内容必须出现在 context_block 的"直播流（窗口）"段。"""
        # 构造一个带 user_nickname 的 NormalizedMessage 鸭子
        msg = MagicMock()
        msg.text = "主播好可爱"
        msg.user_nickname = "观众A"
        msg.user_id = "u_a"
        msg.data_type = "text"
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response({"should_reply": False}))

        await planner.plan([msg], forced=False)

        kwargs = prompt.render_safe.call_args.kwargs
        block = kwargs["context_block"]
        assert "## 直播流（窗口）" in block
        # 弹幕内容透传到段内（planner._render_batch 格式）
        assert "主播好可爱" in block
        assert "观众A" in block

    @pytest.mark.asyncio
    async def test_context_block_contains_environment(self) -> None:
        """直播间快照段含 EnvironmentBlock 渲染字段（未读摘要/key_changes）。"""
        snapshot = MagicMock()
        snapshot.heat = "high"
        snapshot.topics = ["游戏", "上号"]
        snapshot.sc_queue = []
        snapshot.topic_summary = "观众在聊游戏"
        snapshot.topic_summary_at_ms = 0
        snapshot.last_update_ms = 1

        llm = MagicMock()
        llm.call_tools = AsyncMock(return_value=_make_llm_response({"should_reply": False}))
        prompt = MagicMock()
        prompt.render_safe = MagicMock(return_value="PROMPT")
        rs = MagicMock()
        rs.get_snapshot = MagicMock(return_value=snapshot)

        planner = Planner(
            config={"planner_llm": "llm_fast"},
            llm_service=llm,
            prompt_service=prompt,
            room_state=rs,
        )
        await planner.plan([], forced=False)

        kwargs = prompt.render_safe.call_args.kwargs
        block = kwargs["context_block"]
        assert "## 直播间快照" in block
        # EnvironmentBlock 渲染字段透出
        assert "未读摘要" in block
        assert "观众在聊游戏" in block
        # key_changes 透传 topics 列表
        assert "游戏" in block or "上号" in block


class TestPlannerMemoryRecall:
    """§1.50 记忆召回契约测试。"""

    @pytest.mark.asyncio
    async def test_memory_recall_injects_hits_into_context_block(self) -> None:
        """fake memory 返回 hits → context_block 的"记忆召回"段含格式化文本。"""
        from src.modules.memory.models import MemoryHit

        hits = [
            MemoryHit(
                memory_id=1,
                kind="fact",
                text="上周聊过类似游戏话题",
                score=0.87,
                timestamp_ms=1_700_000_000_000,
                metadata={"source": "topic_summary"},
            ),
            MemoryHit(
                memory_id=2,
                kind="fact",
                text="观众A 经常问技术问题",
                score=0.55,
                timestamp_ms=1_700_000_000_000,
                metadata={"source": "live_event"},
            ),
        ]

        memory = MagicMock()
        memory.recall = AsyncMock(return_value=hits)

        snapshot = MagicMock()
        snapshot.heat = "low"
        snapshot.topics = []
        snapshot.sc_queue = []
        snapshot.topic_summary = "游戏讨论"
        snapshot.topic_summary_at_ms = 0
        snapshot.last_update_ms = 1

        llm = MagicMock()
        llm.call_tools = AsyncMock(return_value=_make_llm_response({"should_reply": False}))
        prompt = MagicMock()
        prompt.render_safe = MagicMock(return_value="PROMPT")
        rs = MagicMock()
        rs.get_snapshot = MagicMock(return_value=snapshot)

        planner = Planner(
            config={"planner_llm": "llm_fast"},
            llm_service=llm,
            prompt_service=prompt,
            room_state=rs,
            memory=memory,
            recall_top_k=3,
        )
        await planner.plan([], forced=False)

        # memory.recall 被调过，query 含 topic_summary
        memory.recall.assert_awaited_once()
        call_args = memory.recall.await_args
        assert "游戏讨论" in call_args.args[0]
        assert call_args.kwargs.get("top_k") == 3

        kwargs = prompt.render_safe.call_args.kwargs
        block = kwargs["context_block"]
        # hits 文本透出
        assert "上周聊过类似游戏话题" in block
        assert "观众A 经常问技术问题" in block
        # 格式化约定：score 精度 2 + source 透传
        assert "0.87" in block
        assert "src=topic_summary" in block
        assert "src=live_event" in block

    @pytest.mark.asyncio
    async def test_memory_recall_truncates_long_text(self) -> None:
        """单条 hit 文本超过 80 字需截断（_RECALL_HIT_TEXT_CHARS）。"""
        from src.modules.memory.models import MemoryHit

        long_text = "这是一条非常长的记忆测试文本" * 10  # 超过 80 字符
        hits = [
            MemoryHit(
                memory_id=1,
                kind="fact",
                text=long_text,
                score=0.7,
                timestamp_ms=0,
                metadata={"source": "x"},
            ),
        ]

        memory = MagicMock()
        memory.recall = AsyncMock(return_value=hits)

        snapshot = MagicMock()
        snapshot.heat = "low"
        snapshot.topics = []
        snapshot.sc_queue = []
        snapshot.topic_summary = "t"
        snapshot.topic_summary_at_ms = 0
        snapshot.last_update_ms = 1

        llm = MagicMock()
        llm.call_tools = AsyncMock(return_value=_make_llm_response({"should_reply": False}))
        prompt = MagicMock()
        prompt.render_safe = MagicMock(return_value="PROMPT")
        rs = MagicMock()
        rs.get_snapshot = MagicMock(return_value=snapshot)

        planner = Planner(
            config={"planner_llm": "llm_fast"},
            llm_service=llm,
            prompt_service=prompt,
            room_state=rs,
            memory=memory,
        )
        await planner.plan([], forced=False)

        kwargs = prompt.render_safe.call_args.kwargs
        block = kwargs["context_block"]
        # 长文本被截到 80 字以内（不再展开全文）
        assert long_text not in block
        # 截断标记出现
        assert "…" in block

    @pytest.mark.asyncio
    async def test_memory_none_renders_placeholder_only(self) -> None:
        """memory=None 时流程不炸，且 prompt 含"（暂无）"占位。"""
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response({"should_reply": False}))

        plan = await planner.plan([], forced=False)

        assert plan is not None  # 流程不炸
        kwargs = prompt.render_safe.call_args.kwargs
        # PlannerAssembler 的 memory_recall_section 为空串时填"（暂无）"
        assert "## 记忆召回\n（暂无）" in kwargs["context_block"]

    @pytest.mark.asyncio
    async def test_memory_recall_empty_hits_renders_placeholder(self) -> None:
        """memory 返回空 list → 仍走模板占位（"暂无"），不等同于崩溃。"""
        memory = MagicMock()
        memory.recall = AsyncMock(return_value=[])

        snapshot = MagicMock()
        snapshot.heat = "low"
        snapshot.topics = []
        snapshot.sc_queue = []
        snapshot.topic_summary = "t"
        snapshot.topic_summary_at_ms = 0
        snapshot.last_update_ms = 1

        llm = MagicMock()
        llm.call_tools = AsyncMock(return_value=_make_llm_response({"should_reply": False}))
        prompt = MagicMock()
        prompt.render_safe = MagicMock(return_value="PROMPT")
        rs = MagicMock()
        rs.get_snapshot = MagicMock(return_value=snapshot)

        planner = Planner(
            config={"planner_llm": "llm_fast"},
            llm_service=llm,
            prompt_service=prompt,
            room_state=rs,
            memory=memory,
        )
        await planner.plan([], forced=False)

        memory.recall.assert_awaited_once()
        kwargs = prompt.render_safe.call_args.kwargs
        assert "## 记忆召回\n（暂无）" in kwargs["context_block"]

    @pytest.mark.asyncio
    async def test_memory_recall_exception_does_not_crash(self) -> None:
        """memory.recall 抛异常 → Planner 不炸，返回 DecisionPlan（决策流程降级）。"""
        memory = MagicMock()
        memory.recall = AsyncMock(side_effect=RuntimeError("vector store 炸了"))

        snapshot = MagicMock()
        snapshot.heat = "low"
        snapshot.topics = []
        snapshot.sc_queue = []
        snapshot.topic_summary = "t"
        snapshot.topic_summary_at_ms = 0
        snapshot.last_update_ms = 1

        llm = MagicMock()
        llm.call_tools = AsyncMock(return_value=_make_llm_response({"should_reply": False}))
        prompt = MagicMock()
        prompt.render_safe = MagicMock(return_value="PROMPT")
        rs = MagicMock()
        rs.get_snapshot = MagicMock(return_value=snapshot)

        planner = Planner(
            config={"planner_llm": "llm_fast"},
            llm_service=llm,
            prompt_service=prompt,
            room_state=rs,
            memory=memory,
        )
        plan = await planner.plan([], forced=False)

        # 召回失败不阻断决策——降级到无记忆路径
        assert plan is not None
        assert plan.should_reply is False
        # memory_recall_section 为空 → 模板占位
        kwargs = prompt.render_safe.call_args.kwargs
        assert "## 记忆召回\n（暂无）" in kwargs["context_block"]


class TestPlannerObservability:
    """决策可观测副产品：reply_to 解析 / 静默标记 / 失败原因 / 请求历史指针。"""

    @pytest.mark.asyncio
    async def test_reply_to_parsed_from_llm_output(self) -> None:
        plan_args = {
            "should_reply": True,
            "target": "观众A",
            "reply_to": "abc123",
            "topic_summary": "回应提问",
            "reply_guidance": "回答问题",
            "confidence": 0.9,
        }
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(plan_args))
        plan = await planner.plan([])
        assert plan is not None
        assert plan.reply_to == "abc123"

    @pytest.mark.asyncio
    async def test_reply_to_absent_defaults_none(self) -> None:
        plan_args = {"should_reply": True, "target": "all", "confidence": 0.9}
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(plan_args))
        plan = await planner.plan([])
        assert plan is not None
        assert plan.reply_to is None

    @pytest.mark.asyncio
    async def test_low_confidence_downgrade_marks_silent_reason(self) -> None:
        plan_args = {"should_reply": True, "target": "all", "confidence": 0.0}
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(plan_args))
        plan = await planner.plan([], forced=False)
        assert plan is not None
        assert plan.should_reply is False
        assert plan.silent_reason == "low_confidence", "被裁决压制的静默必须与 LLM 自主沉默可区分"

    @pytest.mark.asyncio
    async def test_last_failure_captures_json_parse_error(self) -> None:
        bad_calls = [{"name": "produce_plan", "arguments": "这不是JSON{"}]
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(None, tool_calls_override=bad_calls))
        plan = await planner.plan([])
        assert plan is None
        assert planner.last_failure is not None
        assert "json_parse_failed" in planner.last_failure

    @pytest.mark.asyncio
    async def test_last_failure_none_on_success(self) -> None:
        plan_args = {"should_reply": False, "confidence": 0.9}
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(plan_args))
        await planner.plan([])
        assert planner.last_failure is None

    @pytest.mark.asyncio
    async def test_raw_content_and_request_id_captured(self) -> None:
        plan_args = {"should_reply": False, "confidence": 0.9}
        resp = _make_llm_response(plan_args)
        resp.request_id = "req_abc123"
        planner, _llm, _prompt, _rs = _make_planner(llm_return=resp)
        await planner.plan([])
        # last_raw_content 现在是 produce_plan arguments 的 JSON 字符串（实际决策结构）
        assert planner.last_raw_content == json.dumps(plan_args, ensure_ascii=False)
        assert planner.last_request_id == "req_abc123"


# =============================================================================
# [context] 组装器路径开关（context_enabled）与召回条数透传
# =============================================================================


class TestPlannerContextEnabled:
    """core.toml [context] 段接线：enabled=False 走裸消息路径、召回条数由配置驱动。"""

    @pytest.mark.asyncio
    async def test_context_disabled_skips_recall_and_assembler(self) -> None:
        """enabled=False → memory.recall 不被调用、context_block 为裸窗口文本。"""
        llm = MagicMock()
        llm.call_tools = AsyncMock(return_value=_make_llm_response({"should_reply": False}))
        prompt = MagicMock()
        prompt.render_safe = MagicMock(return_value="PROMPT")
        rs = MagicMock()
        rs.get_snapshot = MagicMock(return_value=MagicMock(heat="low", topics=[], sc_queue=[]))
        memory = MagicMock()
        memory.recall = AsyncMock(return_value=[])

        planner = Planner(
            config={"planner_llm": "llm_fast"},
            llm_service=llm,
            prompt_service=prompt,
            room_state=rs,
            memory=memory,
            context_enabled=False,
        )
        await planner.plan([], forced=True)

        memory.recall.assert_not_called()
        kwargs = prompt.render_safe.call_args.kwargs
        context_block = kwargs["context_block"]
        # 裸窗口文本不携带组装器的 section 渲染标记
        assert "## 系统人格" not in context_block
        assert "## 可用工具" not in context_block

    @pytest.mark.asyncio
    async def test_recall_top_k_passed_to_memory(self) -> None:
        """recall_top_k 透传给 memory.recall（装配链：[context].memory_recall_long_term）。"""
        llm = MagicMock()
        llm.call_tools = AsyncMock(return_value=_make_llm_response({"should_reply": False}))
        prompt = MagicMock()
        prompt.render_safe = MagicMock(return_value="PROMPT")
        rs = MagicMock()
        rs.get_snapshot = MagicMock(return_value=MagicMock(heat="low", topics=[], sc_queue=[], topic_summary=""))
        memory = MagicMock()
        memory.recall = AsyncMock(return_value=[])

        planner = Planner(
            config={"planner_llm": "llm_fast"},
            llm_service=llm,
            prompt_service=prompt,
            room_state=rs,
            memory=memory,
            recall_top_k=7,
        )
        await planner.plan([], forced=True)

        memory.recall.assert_called_once()
        assert memory.recall.call_args.kwargs.get("top_k") == 7

    @pytest.mark.asyncio
    async def test_context_enabled_keeps_assembler_path(self) -> None:
        """默认（enabled=True）仍走组装器路径：context_block 含 section 渲染标记。"""
        llm = MagicMock()
        llm.call_tools = AsyncMock(return_value=_make_llm_response({"should_reply": False}))
        prompt = MagicMock()
        prompt.render_safe = MagicMock(return_value="PROMPT")
        rs = MagicMock()
        rs.get_snapshot = MagicMock(return_value=MagicMock(heat="low", topics=[], sc_queue=[]))

        planner = Planner(
            config={"planner_llm": "llm_fast"},
            llm_service=llm,
            prompt_service=prompt,
            room_state=rs,
        )
        await planner.plan([], forced=True)

        kwargs = prompt.render_safe.call_args.kwargs
        assert "## 系统人格" in kwargs["context_block"]
