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
        raw = json.dumps({"should_reply": False})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        await planner.plan([], forced=False)

        kwargs = prompt.render_safe.call_args.kwargs
        # 决策侧：必须注入 behavior_style
        assert "behavior_style" in kwargs, (
            "Planner prompt 必须注入 $behavior_style（行动准则）"
        )
        # 身份/表达侧：仍必须隔离
        assert "personality" not in kwargs, "Planner prompt 不得注入 personality（仅 Replyer 消费）"
        assert "style_constraints" not in kwargs, (
            "Planner prompt 不得注入 style_constraints（仅 Replyer 消费）"
        )
        assert "bot_name" not in kwargs, "Planner prompt 不得注入 bot_name（仅 Replyer 消费）"

    @pytest.mark.asyncio
    async def test_plan_behavior_style_default_placeholder_when_empty(self) -> None:
        """behavior_style 未注入时（空串），Planner 应渲染占位文本而非字面 $behavior_style。"""
        raw = json.dumps({"should_reply": False})
        # _make_planner 不传 behavior_style → Planner 内部 _behavior_style=""
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        await planner.plan([], forced=False)

        kwargs = prompt.render_safe.call_args.kwargs
        rendered = kwargs.get("behavior_style")
        assert isinstance(rendered, str), "behavior_style 渲染值必须是字符串"
        # 占位文本包含"未配置"标识，避免字面 ``$behavior_style`` 漏到 prompt
        assert "未配置" in rendered or "行动准则" in rendered, (
            f"behavior_style 空时应渲染占位文本，实际: {rendered!r}"
        )

    @pytest.mark.asyncio
    async def test_plan_behavior_style_propagates_when_set(self) -> None:
        """显式传入 behavior_style 时，渲染值必须如实透传（不做过滤/截断）。"""
        raw = json.dumps({"should_reply": False})
        # 直接构造 Planner 注入 behavior_style
        llm = MagicMock()
        llm.chat = AsyncMock(return_value=_make_llm_response(raw))
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
    """Agenda 上下文注入测试（v2.2：agenda_text 进入 PlannerAssembler 的
    stage_descriptions 字段，最终落地在 ``$context_block`` 的"环节描述"段）。"""

    @pytest.mark.asyncio
    async def test_plan_agenda_text_passed_to_prompt(self) -> None:
        """agenda_text 非空 → 透传到 prompt 的 $context_block 内"环节描述"段。"""
        raw = json.dumps({"should_reply": False})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        agenda_text = "当前环节：开场（1/3）\n任务：自我介绍"
        await planner.plan([], forced=False, agenda_text=agenda_text)

        kwargs = prompt.render_safe.call_args.kwargs
        assert "context_block" in kwargs
        assert agenda_text in kwargs["context_block"]

    @pytest.mark.asyncio
    async def test_plan_no_agenda_text_uses_placeholder(self) -> None:
        """agenda_text=None/空 → 环节描述段使用 "（无）" 占位。"""
        raw = json.dumps({"should_reply": False})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

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
        raw = json.dumps({"should_reply": False})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

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
        raw = json.dumps({"should_reply": False})
        # 构造一个带 user_nickname 的 NormalizedMessage 鸭子
        msg = MagicMock()
        msg.text = "主播好可爱"
        msg.user_nickname = "观众A"
        msg.user_id = "u_a"
        msg.data_type = "text"
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

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
        raw = json.dumps({"should_reply": False})
        snapshot = MagicMock()
        snapshot.heat = "high"
        snapshot.topics = ["游戏", "上号"]
        snapshot.sc_queue = []
        snapshot.topic_summary = "观众在聊游戏"
        snapshot.topic_summary_at_ms = 0
        snapshot.last_update_ms = 1

        llm = MagicMock()
        llm.chat = AsyncMock(return_value=_make_llm_response(raw))
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

        raw = json.dumps({"should_reply": False})
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
        llm.chat = AsyncMock(return_value=_make_llm_response(raw))
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

        raw = json.dumps({"should_reply": False})
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
        llm.chat = AsyncMock(return_value=_make_llm_response(raw))
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
        raw = json.dumps({"should_reply": False})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        plan = await planner.plan([], forced=False)

        assert plan is not None  # 流程不炸
        kwargs = prompt.render_safe.call_args.kwargs
        # PlannerAssembler 的 memory_recall_section 为空串时填"（暂无）"
        assert "## 记忆召回\n（暂无）" in kwargs["context_block"]

    @pytest.mark.asyncio
    async def test_memory_recall_empty_hits_renders_placeholder(self) -> None:
        """memory 返回空 list → 仍走模板占位（"暂无"），不等同于崩溃。"""
        raw = json.dumps({"should_reply": False})
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
        llm.chat = AsyncMock(return_value=_make_llm_response(raw))
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
        raw = json.dumps({"should_reply": False})
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
        llm.chat = AsyncMock(return_value=_make_llm_response(raw))
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