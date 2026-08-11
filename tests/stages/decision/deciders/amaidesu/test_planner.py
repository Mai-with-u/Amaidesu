"""Planner 单元测试（双阶段决策器 Stage 1）。

覆盖：
- 正常路径：forced=True 时返回 DecisionPlan
- 人设隔离：prompt 渲染不传 personality / style_constraints / bot_name
- 客户端选择：使用 planner_client（默认 llm_fast），不与 Replyer 共享
- 无 tools：chat 调用不含 tools 参数
- 降级路径：LLM 异常 / 脏 JSON 均返回 None
- forced 标志透传到 prompt 变量
"""

from __future__ import annotations

# 预导入 config.schemas 种子，规避 deciders/__init__ 的预存在循环导入：
#   deciders/__init__ → llm → llm_decider → schemas/__init__
#   → decision_schemas → llm_decider(尚未完成初始化)
# 让 schemas 入口先完成 llm_decider 的完整初始化，再进入 deciders 包即不再死锁。
# 同样的限制存在于 test_plan.py / test_room_state.py / test_amaidesu_decider.py。
import src.modules.config.schemas  # noqa: F401  # isort:skip

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.stages.decision.deciders.amaidesu.plan import DecisionPlan
from src.stages.decision.deciders.amaidesu.planner import Planner


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------


def _make_llm_response(content: str, *, success: bool = True) -> MagicMock:
    """构造模拟的 LLMResponse（鸭子类型：含 .success / .content 字段）。"""
    m = MagicMock()
    m.success = success
    m.content = content
    m.error = None if success else "mock error"
    return m


def _make_planner(
    *,
    planner_client: str = "llm_fast",
    llm_return: str | None = None,
    llm_raises: Exception | None = None,
) -> tuple[Planner, MagicMock, MagicMock, MagicMock]:
    """构造一个注入 mock 依赖的 Planner。

    Returns:
        (planner, llm_mock, prompt_mock, room_state_mock)
    """
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
        config={"planner_client": planner_client},
        llm_service=llm,
        prompt_service=prompt,
        room_state=rs,
    )
    return planner, llm, prompt, rs


# ---------------------------------------------------------------------------
# 测试 1：forced=True + LLM 返回 should_reply=true → 返回 DecisionPlan
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 测试 2：人设隔离 — prompt 不传 personality / style_constraints / bot_name
# ---------------------------------------------------------------------------


class TestPlannerPersonaIsolation:
    """人设分离承诺：Planner prompt 零人设变量。"""

    @pytest.mark.asyncio
    async def test_plan_no_persona_in_prompt(self) -> None:
        """render_safe 调用 kwargs 中不得包含任何 persona 变量。"""
        raw = json.dumps({"should_reply": False})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        await planner.plan([], forced=False)

        kwargs = prompt.render_safe.call_args.kwargs
        # 人设三件套必须完全缺席
        assert "personality" not in kwargs, "Planner prompt 不得注入 personality"
        assert "style_constraints" not in kwargs, "Planner prompt 不得注入 style_constraints"
        assert "bot_name" not in kwargs, "Planner prompt 不得注入 bot_name"


# ---------------------------------------------------------------------------
# 测试 3：使用 llm_fast 客户端
# ---------------------------------------------------------------------------


class TestPlannerClientType:
    """client_type 选择测试。"""

    @pytest.mark.asyncio
    async def test_plan_uses_llm_fast(self) -> None:
        """默认 planner_client=llm_fast，且透传到 chat 调用。"""
        raw = json.dumps({"should_reply": True})
        planner, llm, _prompt, _rs = _make_planner(
            planner_client="llm_fast",
            llm_return=_make_llm_response(raw),
        )

        await planner.plan([], forced=True)

        assert llm.chat.await_args.kwargs.get("client_type") == "llm_fast"

    @pytest.mark.asyncio
    async def test_plan_uses_configured_client(self) -> None:
        """配置成 llm 时应使用 llm（证明不硬编码）。"""
        raw = json.dumps({"should_reply": True})
        planner, llm, _prompt, _rs = _make_planner(
            planner_client="llm",
            llm_return=_make_llm_response(raw),
        )

        await planner.plan([], forced=True)

        assert llm.chat.await_args.kwargs.get("client_type") == "llm"


# ---------------------------------------------------------------------------
# 测试 4：无 tools 参数
# ---------------------------------------------------------------------------


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
        # 也检查位置参数中没有 tools（chat 签名为 (prompt, *, client_type, ...)）
        args = llm.chat.await_args.args
        assert len(args) <= 1, "chat 只允许 prompt 位置参数"


# ---------------------------------------------------------------------------
# 测试 5：LLM 异常 → None
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 测试 6：脏 JSON → None
# ---------------------------------------------------------------------------


class TestPlannerJsonParsing:
    """JSON 解析测试。"""

    @pytest.mark.asyncio
    async def test_plan_invalid_json_returns_none(self) -> None:
        """脏 JSON → 解析失败 → 返回 None。"""
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response("这不是 JSON，是主播的胡言乱语"))

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


# ---------------------------------------------------------------------------
# 测试 7：forced 标志透传到 prompt
# ---------------------------------------------------------------------------


class TestPlannerForcedFlag:
    """forced 标志传播测试。"""

    @pytest.mark.asyncio
    async def test_plan_forced_flag_passed_to_prompt(self) -> None:
        """forced 标志的 True/False 值应区分传递到 prompt 变量。

        实现把 forced 转为小写字符串（对齐模板文档 `forced=true/false` 约定），
        因此 forced=True → "true"，forced=False → "false"。
        """
        # True → "true"
        raw_t = json.dumps({"should_reply": True})
        planner_t, _llm_t, prompt_t, _rs_t = _make_planner(llm_return=_make_llm_response(raw_t))
        await planner_t.plan([], forced=True)
        kwargs_t = prompt_t.render_safe.call_args.kwargs
        assert "forced" in kwargs_t, "render_safe 必须接收 forced 变量"
        # 接受布尔 True 或字符串 "true"
        forced_true = kwargs_t["forced"]
        assert forced_true in (True, "true"), f"forced=True 时 prompt 变量应为 True 或 'true'，实际: {forced_true!r}"

        # False → "false"（与 True 必须区分）
        raw_f = json.dumps({"should_reply": False})
        planner_f, _llm_f, prompt_f, _rs_f = _make_planner(llm_return=_make_llm_response(raw_f))
        await planner_f.plan([], forced=False)
        kwargs_f = prompt_f.render_safe.call_args.kwargs
        assert "forced" in kwargs_f
        forced_false = kwargs_f["forced"]
        assert forced_false in (False, "false"), (
            f"forced=False 时 prompt 变量应为 False 或 'false'，实际: {forced_false!r}"
        )
        # 两次调用的 forced 值必须不同（证明标志确实被透传，而非硬编码）
        assert forced_true != forced_false, f"forced=True/False 的 prompt 变量必须不同，实际均为 {forced_true!r}"


# ---------------------------------------------------------------------------
# 测试 8：会话历史注入（反重复）
# ---------------------------------------------------------------------------


class _FakeHistoryMsg:
    """模拟 ``ConversationMessage`` 鸭子类型（仅含 ``role`` / ``content`` 属性）。"""

    def __init__(self, role: Any, content: str) -> None:
        self.role = role
        self.content = content


class TestPlannerHistoryInjection:
    """会话历史注入测试（让 Planner 决策时能看到最近对话，反重复）。

    覆盖：
    - 有效 history → render_safe 收到 ``conversation_history``，内容为
      ``user:`` / ``assistant:`` 交替的多行文本（顺序保持旧→新）。
    - ``history=None`` → 占位文本 "（暂无对话历史）"。
    - ``history=[]`` → 同上占位文本。
    - 默认不传 history → 占位文本（与现有测试兼容）。
    - ``role`` 为 ``MessageRole`` 枚举时，使用 ``role.value``（而非 ``str(role)``）。
    """

    @pytest.mark.asyncio
    async def test_plan_with_history_renders_user_assistant_lines(self) -> None:
        """传入 history → render_safe 收到 ``conversation_history``，``user:`` / ``assistant:`` 交替。"""
        raw = json.dumps({"should_reply": False})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        history = [
            _FakeHistoryMsg("user", "主播打游戏好厉害"),
            _FakeHistoryMsg("assistant", "哈哈谢谢支持"),
            _FakeHistoryMsg("user", "今天玩什么游戏"),
            _FakeHistoryMsg("assistant", "今天玩只狼"),
        ]

        await planner.plan([], forced=False, history=history)

        kwargs = prompt.render_safe.call_args.kwargs
        assert "conversation_history" in kwargs, "render_safe 必须接收 conversation_history 变量"
        rendered = kwargs["conversation_history"]

        assert rendered.count("\n") == 3, f"历史应有 4 行（3 个换行），实际换行数: {rendered.count(chr(10))}"

        assert "user: 主播打游戏好厉害" in rendered
        assert "assistant: 哈哈谢谢支持" in rendered
        assert "user: 今天玩什么游戏" in rendered
        assert "assistant: 今天玩只狼" in rendered

        idx0 = rendered.index("user: 主播打游戏好厉害")
        idx1 = rendered.index("assistant: 哈哈谢谢支持")
        idx2 = rendered.index("user: 今天玩什么游戏")
        idx3 = rendered.index("assistant: 今天玩只狼")
        assert idx0 < idx1 < idx2 < idx3, "历史应按传入顺序（旧→新）拼接"

    @pytest.mark.asyncio
    async def test_plan_with_role_enum_uses_value(self) -> None:
        """``role`` 为 ``MessageRole`` 枚举时，使用 ``role.value``（而非 ``str(role)``）。"""
        import enum

        class _FakeRole(enum.Enum):
            USER = "user"
            ASSISTANT = "assistant"

        raw = json.dumps({"should_reply": False})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        history = [
            _FakeHistoryMsg(_FakeRole.USER, "一条弹幕"),
            _FakeHistoryMsg(_FakeRole.ASSISTANT, "主播回复"),
        ]

        await planner.plan([], forced=False, history=history)

        rendered = prompt.render_safe.call_args.kwargs["conversation_history"]
        assert "user: 一条弹幕" in rendered
        assert "assistant: 主播回复" in rendered
        assert "_FakeRole" not in rendered, f"role 枚举不应被 repr 化，实际: {rendered!r}"

    @pytest.mark.asyncio
    async def test_plan_with_none_history_uses_placeholder(self) -> None:
        """``history=None`` → render_safe 收到 ``conversation_history="（暂无对话历史）"``。"""
        raw = json.dumps({"should_reply": False})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        await planner.plan([], forced=False, history=None)

        kwargs = prompt.render_safe.call_args.kwargs
        assert "conversation_history" in kwargs
        assert kwargs["conversation_history"] == "（暂无对话历史）", (
            f"history=None 时应使用占位文本，实际: {kwargs['conversation_history']!r}"
        )

    @pytest.mark.asyncio
    async def test_plan_with_empty_history_uses_placeholder(self) -> None:
        """``history=[]`` → 同样使用占位文本（空列表与 None 等价）。"""
        raw = json.dumps({"should_reply": False})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        await planner.plan([], forced=False, history=[])

        kwargs = prompt.render_safe.call_args.kwargs
        assert "conversation_history" in kwargs
        assert kwargs["conversation_history"] == "（暂无对话历史）", (
            f"history=[] 时应使用占位文本，实际: {kwargs['conversation_history']!r}"
        )

    @pytest.mark.asyncio
    async def test_plan_default_no_history_arg_uses_placeholder(self) -> None:
        """不传 ``history`` → 默认占位文本（与现有测试兼容，不破坏既有行为）。"""
        raw = json.dumps({"should_reply": False})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        await planner.plan([], forced=False)

        kwargs = prompt.render_safe.call_args.kwargs
        assert "conversation_history" in kwargs
        assert kwargs["conversation_history"] == "（暂无对话历史）", (
            f"不传 history 时应使用占位文本，实际: {kwargs['conversation_history']!r}"
        )

    @pytest.mark.asyncio
    async def test_plan_history_coexists_with_proactive(self) -> None:
        """``history`` 与 ``proactive`` 同时透传，互不干扰（反重复生效路径）。"""
        raw = json.dumps({"should_reply": True})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        history = [
            _FakeHistoryMsg("user", "观众问 1"),
            _FakeHistoryMsg("assistant", "主播答 1"),
        ]

        await planner.plan([], proactive=True, history=history)

        kwargs = prompt.render_safe.call_args.kwargs
        assert kwargs.get("proactive") == "true"
        rendered = kwargs["conversation_history"]
        assert "user: 观众问 1" in rendered
        assert "assistant: 主播答 1" in rendered


# ---------------------------------------------------------------------------
# 测试 9：决策一致性校验（低置信度降级静默）
# ---------------------------------------------------------------------------


class TestPlannerConfidenceGate:
    """``should_reply=true`` 但 ``confidence`` 过低 → 降级静默（P0）。

    回归场景（日志实测）：proactive 冷场触发时 LLM 曾输出
    ``{"should_reply": true, "confidence": 0.00}`` 的矛盾决策，导致
    主播"没话找话硬开口"。强制场景（SC/礼物/上舰）不受此限制。
    """

    @pytest.mark.asyncio
    async def test_low_confidence_non_forced_silenced(self) -> None:
        """非 forced + 低置信度 + should_reply=true → 降级为 should_reply=False"""
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
    async def test_low_confidence_non_forced_proactive_silenced(self) -> None:
        """proactive 场景同样受校验（实测 08-05 的 confidence=0.00 即发生在冷场主动发言）"""
        raw = json.dumps({"should_reply": True, "confidence": 0.1})
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        plan = await planner.plan([], proactive=True)

        assert plan is not None
        assert plan.should_reply is False, "proactive 低置信度也应降级静默"

    @pytest.mark.asyncio
    async def test_low_confidence_forced_keeps_reply(self) -> None:
        """forced 场景不受校验：SC/礼物/上舰即使置信度低也必须回应"""
        raw = json.dumps({"should_reply": True, "target": "sc_user", "confidence": 0.0})
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        plan = await planner.plan([], forced=True)

        assert plan is not None
        assert plan.should_reply is True, "forced 场景不应降级"
        assert plan.target == "sc_user"

    @pytest.mark.asyncio
    async def test_high_confidence_non_forced_keeps_reply(self) -> None:
        """非 forced + 高置信度 → 正常回复"""
        raw = json.dumps({"should_reply": True, "target": "all", "confidence": 0.9})
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        plan = await planner.plan([], forced=False)

        assert plan is not None
        assert plan.should_reply is True

    @pytest.mark.asyncio
    async def test_confidence_at_threshold_keeps_reply(self) -> None:
        """置信度恰好等于阈值 0.3 时不应降级（边界 >= 语义）"""
        raw = json.dumps({"should_reply": True, "confidence": 0.3})
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        plan = await planner.plan([], forced=False)

        assert plan is not None
        assert plan.should_reply is True, "confidence=0.3 恰好等于阈值不应降级"


# ---------------------------------------------------------------------------
# 测试 10：摘要年龄标注
# ---------------------------------------------------------------------------


class TestPlannerSummaryAge:
    """Planner prompt 中话题摘要附带生成年龄（P1-6）"""

    @pytest.mark.asyncio
    async def test_topic_summary_rendered_with_age(self) -> None:
        """态势中话题摘要行包含 'X前更新' 年龄标注"""
        raw = json.dumps({"should_reply": False})
        llm = MagicMock()
        llm.chat = AsyncMock(return_value=_make_llm_response(raw))
        prompt = MagicMock()
        prompt.render_safe = MagicMock(return_value="PROMPT")

        rs = MagicMock()
        snap = MagicMock(
            heat="low",
            topics=[],
            sc_queue=[],
            last_update_ms=102_000,
            topic_summary="观众在问游戏",
            topic_summary_at_ms=100_000,
        )
        rs.get_snapshot = MagicMock(return_value=snap)

        planner = Planner(
            config={"planner_client": "llm_fast"},
            llm_service=llm,
            prompt_service=prompt,
            room_state=rs,
        )
        await planner.plan([], forced=False)

        room_state_text = prompt.render_safe.call_args.kwargs["room_state"]
        assert "观众在问游戏" in room_state_text
        assert "前更新" in room_state_text, f"摘要行应含年龄标注: {room_state_text}"

    @pytest.mark.asyncio
    async def test_no_age_when_summary_missing(self) -> None:
        """摘要为空时不渲染摘要行（无年龄标注）"""
        raw = json.dumps({"should_reply": False})
        llm = MagicMock()
        llm.chat = AsyncMock(return_value=_make_llm_response(raw))
        prompt = MagicMock()
        prompt.render_safe = MagicMock(return_value="PROMPT")

        rs = MagicMock()
        snap = MagicMock(heat="low", topics=[], sc_queue=[], last_update_ms=102_000, topic_summary="")
        rs.get_snapshot = MagicMock(return_value=snap)

        planner = Planner(
            config={"planner_client": "llm_fast"},
            llm_service=llm,
            prompt_service=prompt,
            room_state=rs,
        )
        await planner.plan([], forced=False)

        room_state_text = prompt.render_safe.call_args.kwargs["room_state"]
        assert "话题摘要" not in room_state_text
        assert "前更新" not in room_state_text
