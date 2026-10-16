"""StreamerAgent.debug_test_decision 调试门面：入参校验、弹幕/主动两种模式与决策结果视图"""

from __future__ import annotations

from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.tools.models import ToolExecutionResult
from tests.agents.streamer.helpers import make_streamer_agent

_REPLY: Dict[str, Any] = {"speech": "欢迎来到直播间！", "emotion": {"name": "happy", "intensity": 0.5}, "actions": []}


def _agent(outcome: Optional[Dict[str, Any]], reply: Optional[Dict[str, Any]] = None):
    """Planner 直接给出本轮结论；reply 工具按 reply 返回成功或失败。"""
    agent = make_streamer_agent()
    agent._rounds._planner.plan = AsyncMock(return_value=outcome)
    result = (
        ToolExecutionResult(tool_name="reply", success=True, structured_content=reply)
        if reply is not None
        else ToolExecutionResult(tool_name="reply", success=False, error_message="LLM 超时")
    )
    agent._reply_provider = MagicMock()
    agent._reply_provider.invoke = AsyncMock(return_value=result)
    return agent


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"batch": [{"nickname": "观众", "text": "你好"}], "proactive": True}, "proactive"),
        ({"batch": []}, "弹幕批次为空"),
        ({"batch": None}, ""),
        ({"batch": [{"nickname": "观众", "text": "   "}]}, ""),  # 全空白等同空批
    ],
)
async def test_invalid_input_is_rejected_before_deciding(kwargs: dict, error: str) -> None:
    agent = _agent({"replied": False})
    result = await agent.debug_test_decision(**kwargs)
    assert result["success"] is False and error in result["error"]
    agent._planner.plan.assert_not_awaited()


async def test_danmaku_mode_returns_full_view() -> None:
    outcome = {
        "replied": True,
        "target": "debug_测试观众",
        "reply_to": "debug_测试观众",
        "topic_summary": "打招呼",
        "reply_guidance": "友好回应",
        "speech": "欢迎来到直播间！",
        "emotion": "happy",
        "reply_payload": _REPLY,
    }
    agent = _agent(outcome, _REPLY)
    result = await agent.debug_test_decision(batch=[{"nickname": "测试观众", "text": "主播好"}], forced=False)

    assert (result["success"], result["error"], result["trigger_reason"]) == (True, None, "dashboard:debug_test")
    assert result["elapsed_ms"] >= 0 and result["utterance_id"].startswith("utt_")
    plan = result["plan"]
    assert (plan["should_reply"], plan["topic_summary"]) == (True, "打招呼")
    assert (result["speech"], result["emotion"]) == ("欢迎来到直播间！", "happy")

    call = agent._planner.plan.await_args
    (message,) = call.args[0]
    assert (message.content, message.user.name) == ("主播好", "测试观众")
    assert (call.kwargs["forced"], call.kwargs["proactive"]) == (False, False)


async def test_missing_nickname_defaults_to_placeholder() -> None:
    agent = _agent({"replied": False, "silent_reason": "natural"})
    await agent.debug_test_decision(batch=[{"text": "你好"}])
    assert agent._planner.plan.await_args.args[0][0].user.name == "测试观众"


@pytest.mark.parametrize(
    ("outcome", "error_prefix"),
    [
        ({"replied": False, "silent_reason": "natural", "topic_summary": "闲聊"}, None),
        (None, "planner_failed"),  # LLM 异常/脏 JSON 降级
        # reply 失败发生在 Planner ReAct 循环内，经结论里的 error 带出
        ({"replied": False, "error": "reply_tool_failed: LLM 超时"}, "planner_failed"),
    ],
    ids=["silent", "planner_failed", "reply_tool_failed"],
)
async def test_no_speech_outcomes(outcome, error_prefix) -> None:
    """门面调用本身成功；不说话是如实回传，决策失败由 error 表达。"""
    agent = _agent(outcome)
    result = await agent.debug_test_decision(batch=[{"nickname": "路人", "text": "……"}])

    assert result["success"] is True and result["speech"] is None
    if error_prefix is None:
        assert (result["error"], result["utterance_id"], result["plan"]["should_reply"]) == (None, None, False)
        agent._reply_provider.invoke.assert_not_awaited()
    else:
        assert result["error"].startswith(error_prefix)
    if outcome and outcome.get("error"):
        assert "reply_tool_failed: LLM 超时" in result["error"]


async def test_proactive_mode_runs_without_rate_limit() -> None:
    """主动直跑：空批 + proactive=True 进决策，不经 ProactiveTrigger 限流（主动发言关闭也能跑）。"""
    agent = _agent(
        {"replied": True, "topic_summary": "主动话题", "speech": _REPLY["speech"], "reply_payload": _REPLY}, _REPLY
    )
    result = await agent.debug_test_decision(proactive=True)

    assert (result["success"], result["speech"]) == (True, "欢迎来到直播间！")
    assert result["trigger_reason"] == "proactive:dashboard_debug"
    call = agent._planner.plan.await_args
    assert call.args[0] == [] and call.kwargs["proactive"] is True
