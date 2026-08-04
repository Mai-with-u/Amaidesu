"""Planner 主动发言参数透传测试（Proactive Speech Task 3）。

覆盖 `Planner.plan(..., proactive=bool)` 的三件事：

- `proactive=True` 时，``prompt_service.render_safe`` 必须收到 ``proactive="true"``
- 默认调用（即 ``proactive=False``）必须收到 ``proactive="false"``
- ``proactive=True`` + 空批次时仍能返回合法 ``DecisionPlan``（模板声明
  "本批弹幕可能是 0 条"，空批次是被支持的路径）

所有用例 mock LLM 与 prompt_service，不发起任何真实网络调用。
"""

from __future__ import annotations

# 预导入 config.schemas 种子，规避 deciders/__init__ 的预存在循环导入：
#   deciders/__init__ → llm → llm_decider → schemas/__init__
#   → decision_schemas → llm_decider(尚未完成初始化)
# 与 test_planner.py / test_plan.py / test_room_state.py 保持同一规避顺序。
import src.modules.config.schemas  # noqa: F401  # isort:skip

import json
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
) -> tuple[Planner, MagicMock, MagicMock, MagicMock]:
    """构造一个注入 mock 依赖的 Planner。

    Returns:
        (planner, llm_mock, prompt_mock, room_state_mock)
    """
    llm = MagicMock()
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
# 测试 1：proactive=True 透传到 prompt 为小写字符串 "true"
# ---------------------------------------------------------------------------


class TestPlannerProactiveFlag:
    """``proactive`` 关键字参数透传测试。"""

    @pytest.mark.asyncio
    async def test_plan_proactive_true_passed_to_prompt(self) -> None:
        """``proactive=True`` → render_safe 必须收到 ``proactive="true"``。"""
        raw = json.dumps({"should_reply": False})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        await planner.plan([], proactive=True)

        kwargs = prompt.render_safe.call_args.kwargs
        assert "proactive" in kwargs, "render_safe 必须接收 proactive 变量"
        # 实现将 bool 序列化为小写字符串，对齐模板中的文档约定
        assert kwargs["proactive"] == "true", (
            f"proactive=True 时 prompt 变量应为 'true'，实际: {kwargs['proactive']!r}"
        )


# ---------------------------------------------------------------------------
# 测试 2：默认调用（proactive 缺省）透传为 "false"
# ---------------------------------------------------------------------------


class TestPlannerProactiveDefault:
    """``proactive`` 缺省值透传测试。"""

    @pytest.mark.asyncio
    async def test_plan_proactive_default_is_false(self) -> None:
        """不传 ``proactive`` → render_safe 收到 ``proactive="false"``。

        Planner 保持 keyword-only 参数语义，调用方（Decider 编排层）当前总是显式
        传入 ``proactive``；本测试保护默认值仍是 ``False``，避免无意翻转。
        """
        raw = json.dumps({"should_reply": False})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        await planner.plan([])

        kwargs = prompt.render_safe.call_args.kwargs
        assert "proactive" in kwargs
        assert kwargs["proactive"] == "false", (
            f"proactive 缺省时 prompt 变量应为 'false'，实际: {kwargs['proactive']!r}"
        )


# ---------------------------------------------------------------------------
# 测试 3：空批次 + proactive=True 仍返回合法 DecisionPlan
# ---------------------------------------------------------------------------


class TestPlannerProactiveEmptyBatch:
    """``proactive=True`` + 空批次仍能产出 DecisionPlan。

    模板 ``amaidesu_planner_v2`` 已声明"本批弹幕可能是 0 条"，因此 Planner 在
    主动发言场景下空批次完全合法：LLM 应基于房间状态独立判断是否开口。
    """

    @pytest.mark.asyncio
    async def test_plan_proactive_empty_batch_returns_plan(self) -> None:
        """空批次 + proactive=True + LLM 返回合法 JSON → 返回 DecisionPlan。"""
        raw = json.dumps(
            {
                "should_reply": True,
                "target": "room",
                "topic_summary": "主动寒暄",
                "reply_guidance": "基于房间状态开口",
                "confidence": 0.6,
            }
        )
        planner, _llm, _prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        plan = await planner.plan([], proactive=True)

        assert plan is not None, "空批次 + proactive=True 必须能产出 DecisionPlan"
        assert isinstance(plan, DecisionPlan)
        assert plan.should_reply is True
        assert plan.target == "room"
        assert plan.topic_summary == "主动寒暄"
        assert plan.reply_guidance == "基于房间状态开口"
        assert plan.confidence == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# 测试 4：forced 与 proactive 两个标志同时透传且值独立
# ---------------------------------------------------------------------------


class TestPlannerForcedAndProactiveCoexist:
    """``forced`` 与 ``proactive`` 互不干扰，应同时透传。"""

    @pytest.mark.asyncio
    async def test_plan_forced_and_proactive_both_passed(self) -> None:
        """``forced=True, proactive=True`` → render_safe 同时收到 ``forced="true"`` 与 ``proactive="true"``。"""
        raw = json.dumps({"should_reply": True})
        planner, _llm, prompt, _rs = _make_planner(llm_return=_make_llm_response(raw))

        await planner.plan([], forced=True, proactive=True)

        kwargs = prompt.render_safe.call_args.kwargs
        assert kwargs.get("forced") == "true", (
            f"forced=True 应透传为 'true'，实际: {kwargs.get('forced')!r}"
        )
        assert kwargs.get("proactive") == "true", (
            f"proactive=True 应透传为 'true'，实际: {kwargs.get('proactive')!r}"
        )