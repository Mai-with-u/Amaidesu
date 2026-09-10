"""StreamerAgent.debug_test_decision 调试门面测试

覆盖：
1. 入参校验：proactive + batch 互斥拒绝；非 proactive 空 batch 拒绝。
2. 弹幕模式：构造 NormalizedMessage 后走真实两阶段决策，
   门面返回视图含 plan / speech / utterance_id / elapsed_ms。
3. Planner 拒绝：plan.should_reply=false 如实回传（speech=None，不算错误）。
4. Planner 失败（返回 None）：error 回传 planner_failed。
5. Reply 工具失败：error 回传 reply_tool_failed。
6. proactive 直跑模式：_make_two_stage_decision 收到空批 + proactive=True。

Y 模型：reply_tool.invoke 返回 ``ToolExecutionResult(success, structured_content=dict)``（不再是 content JSON 字符串）。
本测试 patch ``agent._reply_provider.invoke`` 返回 ``structured_content`` dict，
由 ``_dispatch_speech_and_emotion`` 直接消费。

测试方法：mock Planner 与 ReplyToolProvider（替换 agent 内部组件），
不触发真实 LLM；事件/历史 fire-and-forget 任务用 asyncio.sleep 收尾。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.streamer_agent import StreamerAgent, StreamerAgentConfig
from src.modules.llm.manager import LLMResponse


def _make_agent_config(**overrides: Any) -> StreamerAgentConfig:
    defaults: Dict[str, Any] = {
        "planner_llm": "llm_fast",
        "replyer_llm": "llm",
        "proactive_enabled": False,
        "profanity_enabled": False,
        "batch_window_ms": 100,
        "tick_interval_ms": 50,
    }
    defaults.update(overrides)
    return StreamerAgentConfig(**defaults)


def _build_agent() -> StreamerAgent:
    """构造最小化 StreamerAgent（mock LLM / prompt / context）。"""
    llm = MagicMock()
    llm.call_tools = AsyncMock(return_value=LLMResponse(success=False, error="not used"))
    llm.chat = AsyncMock()
    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="PROMPT")
    ctx = MagicMock()
    ctx.get_history = AsyncMock(return_value=[])
    ctx.add_message = AsyncMock(return_value=None)
    return StreamerAgent(
        config=_make_agent_config(),
        llm_manager=llm,
        prompt_manager=prompt,
        context_service=ctx,
        event_bus=None,
        tool_registry=None,
    )


def _patch_planner(agent: StreamerAgent, outcome: Optional[Dict[str, Any]]) -> None:
    agent._planner.plan = AsyncMock(return_value=outcome)


def _patch_reply_provider(
    agent: StreamerAgent, structured: Optional[Dict[str, Any]], error: Optional[str] = None
) -> None:
    """替换 reply Provider.invoke：返回携带 structured_content/error 的 ToolExecutionResult 替身。

    Y 模型：成功时 ``structured_content`` 是 Replyer 返回的 dict（speech/emotion/actions/metadata），
    失败时 ``success=False`` + ``error_message``。
    """
    from src.modules.tools.models import ToolExecutionResult

    if structured is not None:
        result = ToolExecutionResult(
            tool_name="reply",
            success=True,
            structured_content=structured,
        )
    else:
        result = ToolExecutionResult(
            tool_name="reply",
            success=False,
            error_message=error or "",
        )
    provider = MagicMock()
    provider.invoke = AsyncMock(return_value=result)
    agent._reply_provider = provider


_REPLY_STRUCTURED: Dict[str, Any] = {
    "speech": "欢迎来到直播间！",
    "emotion": {"name": "happy", "intensity": 0.5},
    "actions": [],
    "metadata": {},
}


# ---------------------------------------------------------------------------
# 入参校验
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejects_proactive_with_batch():
    agent = _build_agent()
    result = await agent.debug_test_decision(
        batch=[{"nickname": "观众", "text": "你好"}],
        proactive=True,
    )
    assert result["success"] is False
    assert "proactive" in result["error"]


@pytest.mark.asyncio
async def test_rejects_empty_batch():
    agent = _build_agent()
    result = await agent.debug_test_decision(batch=[])
    assert result["success"] is False
    assert "弹幕批次为空" in result["error"]

    result_none = await agent.debug_test_decision(batch=None)
    assert result_none["success"] is False


@pytest.mark.asyncio
async def test_rejects_batch_of_empty_texts():
    """全部 text 为空白的批次等同空批（校验在前，不进决策）。"""
    agent = _build_agent()
    result = await agent.debug_test_decision(batch=[{"nickname": "观众", "text": "   "}])
    assert result["success"] is False


# ---------------------------------------------------------------------------
# 弹幕模式：成功路径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_danmaku_mode_success_returns_full_view():
    agent = _build_agent()
    _patch_planner(
        agent,
        {
            "replied": True,
            "target": "debug_测试观众",
            "reply_to": "debug_测试观众",
            "topic_summary": "打招呼",
            "reply_guidance": "友好回应",
            "confidence": 0.9,
            "speech": "欢迎来到直播间！",
            "emotion": "happy",
            "reply_payload": _REPLY_STRUCTURED,
        },
    )
    _patch_reply_provider(agent, _REPLY_STRUCTURED)

    result = await agent.debug_test_decision(
        batch=[{"nickname": "测试观众", "text": "主播好"}],
        forced=False,
    )

    assert result["success"] is True
    assert result["error"] is None
    assert result["elapsed_ms"] >= 0
    assert result["plan"]["should_reply"] is True
    assert result["plan"]["confidence"] == 0.9
    assert result["plan"]["topic_summary"] == "打招呼"
    assert result["speech"] == "欢迎来到直播间！"
    assert result["emotion"] == "happy"
    assert result["utterance_id"].startswith("utt_")
    assert result["trigger_reason"] == "dashboard:debug_test"

    # Planner 收到的批次已转 NormalizedMessage（昵称/文本透传）
    plan_call = agent._planner.plan.await_args
    batch = plan_call.args[0]
    assert len(batch) == 1
    assert batch[0].text == "主播好"
    assert batch[0].user_nickname == "测试观众"
    assert plan_call.kwargs["forced"] is False
    assert plan_call.kwargs["proactive"] is False

    # fire-and-forget 任务收尾（写历史等；无 event_bus 不 emit）
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_danmaku_default_nickname_when_missing():
    """nickname 缺省时用「测试观众」占位。"""
    agent = _build_agent()
    _patch_planner(agent, {"replied": False, "silent_reason": "natural"})
    _patch_reply_provider(agent, None)

    await agent.debug_test_decision(batch=[{"text": "你好"}])
    batch = agent._planner.plan.await_args.args[0]
    assert batch[0].user_nickname == "测试观众"


# ---------------------------------------------------------------------------
# Planner 拒绝 / 失败
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_rejected_returns_plan_without_speech():
    agent = _build_agent()
    _patch_planner(
        agent,
        {"replied": False, "silent_reason": "natural", "topic_summary": "闲聊"},
    )
    _patch_reply_provider(agent, None)

    result = await agent.debug_test_decision(batch=[{"nickname": "路人", "text": "……"}])

    assert result["success"] is True
    assert result["plan"]["should_reply"] is False
    assert result["speech"] is None
    assert result["utterance_id"] is None
    assert result["error"] is None
    # Replyer 未被触发
    agent._reply_provider.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_planner_failure_returns_error():
    agent = _build_agent()
    _patch_planner(agent, None)  # LLM 异常/脏 JSON 降级
    _patch_reply_provider(agent, None)

    result = await agent.debug_test_decision(batch=[{"nickname": "观众", "text": "你好"}])

    assert result["success"] is True  # 门面调用成功；决策本身失败由 error 表达
    assert result["error"] is not None
    assert result["error"].startswith("planner_failed")
    assert result["plan"] is None


# ---------------------------------------------------------------------------
# Reply 工具失败
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_tool_failure_returns_error():
    agent = _build_agent()
    _patch_planner(
        agent,
        {"replied": False, "error": "reply_tool_failed: LLM 超时"},
    )
    _patch_reply_provider(agent, None, error="LLM 超时")

    result = await agent.debug_test_decision(batch=[{"nickname": "观众", "text": "你好"}])

    assert result["success"] is True
    assert result["error"] is not None
    # reply 失败发生在 Planner ReAct 循环内，错误经 planner_failed 前缀带出
    assert "reply_tool_failed: LLM 超时" in result["error"]
    assert result["speech"] is None


# ---------------------------------------------------------------------------
# proactive 直跑模式
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proactive_mode_bypasses_rate_limit():
    """proactive=True 直跑：空批 + proactive=True 进决策，
    不经过 ProactiveTrigger 限流（proactive_enabled=False 也能跑）。"""
    agent = _build_agent()  # config.proactive_enabled=False
    _patch_planner(
        agent,
        {
            "replied": True,
            "topic_summary": "主动话题",
            "speech": "欢迎来到直播间！",
            "reply_payload": _REPLY_STRUCTURED,
        },
    )
    _patch_reply_provider(agent, _REPLY_STRUCTURED)

    result = await agent.debug_test_decision(proactive=True)

    assert result["success"] is True
    assert result["speech"] == "欢迎来到直播间！"

    plan_call = agent._planner.plan.await_args
    assert plan_call.args[0] == []  # 空批次
    assert plan_call.kwargs["proactive"] is True
    assert result["trigger_reason"] == "proactive:dashboard_debug"
    await asyncio.sleep(0.05)
