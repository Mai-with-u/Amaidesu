"""Planner 回看自己——最近几轮说出口的打算与补充过的要求进参考段，防止连着几轮讲同一件事。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.llm.payload import Response, ToolCall
from src.modules.tools.models import ToolExecutionResult, ToolSpec
from src.modules.tools.provider import make_provider_from_specs
from src.modules.tools.registry import ToolRegistry


def _reply_call(topic: str, guidance: str) -> Response:
    return Response(
        success=True,
        content="",
        tool_calls=[
            ToolCall(id="c1", name="streamer_reply", arguments={"topic_summary": topic, "reply_guidance": guidance})
        ],
    )


def _planner(
    responses: List[Response],
    *,
    reply_ok: bool = True,
    config: Optional[Dict[str, Any]] = None,
) -> Planner:
    """真注册表 + 可切换成败的 reply；上下文组装器关闭，只看情境段。"""
    registry = ToolRegistry()
    reply_result = ToolExecutionResult(
        tool_name="streamer_reply",
        success=reply_ok,
        structured_content={"speech": "说了", "emotion": {"name": "happy"}, "metadata": {}} if reply_ok else None,
        error_message=None if reply_ok else "Replyer 返回 None",
    )

    async def _reply(_invocation: Any) -> ToolExecutionResult:
        return reply_result

    spec = ToolSpec(name="reply", description="主播发言出口", kind="sync", provider="streamer")
    registry.register_provider(
        make_provider_from_specs("streamer", [(spec, _reply)]), visible_to={"streamer_reply": ["streamer"]}
    )
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=list(responses))
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    return Planner(
        config=config or {},
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
        context_enabled=False,
        reply_provider=MagicMock(),
    )


async def _reference(planner: Planner) -> str:
    text = await planner._assemble_reference([], None, None, False, False, "")
    assert text is not None
    return text


@pytest.mark.asyncio
async def test_spoken_intents_listed_oldest_first() -> None:
    """说出口的每轮话题与指引按先后列进【我最近几轮想说的】，下一窗就看得到自己连着讲了什么。"""
    planner = _planner([_reply_call("报平安：满血了", "轻松"), _reply_call("还是满血", "得意")])

    await planner.plan([], proactive=True)
    await planner.plan([], proactive=True)
    text = await _reference(planner)

    assert "【我最近几轮想说的】" in text
    section = text.split("【我最近几轮想说的】", 1)[1].splitlines()[1:]
    assert section == ["- 刚刚 话题：报平安：满血了 ｜ 指引：轻松", "- 刚刚 话题：还是满血 ｜ 指引：得意"]


@pytest.mark.asyncio
async def test_failed_reply_is_not_recorded() -> None:
    """表达失败（观众没听到）的打算不算说过。"""
    planner = _planner([_reply_call("报平安", "轻松")], reply_ok=False, config={"planner_max_steps": 1})

    await planner.plan([], proactive=True)

    assert "【我最近几轮想说的】" not in await _reference(planner)


@pytest.mark.asyncio
async def test_intents_keep_only_configured_count() -> None:
    """只回看配置的轮数，最早的先出列。"""
    planner = _planner(
        [_reply_call(f"话题{i}", "") for i in range(3)],
        config={"planner_recent_intents_max": 2},
    )

    for _ in range(3):
        await planner.plan([], proactive=True)
    text = await _reference(planner)

    assert "话题0" not in text and "话题1" in text and "话题2" in text


@pytest.mark.asyncio
async def test_zero_intents_disables_section() -> None:
    """配置为 0 时不列这一段。"""
    planner = _planner([_reply_call("报平安", "")], config={"planner_recent_intents_max": 0})

    await planner.plan([], proactive=True)

    assert "【我最近几轮想说的】" not in await _reference(planner)


def _relay_planner(result: ToolExecutionResult) -> tuple[Planner, MagicMock]:
    registry = MagicMock()
    registry.invoke = AsyncMock(return_value=result)
    return Planner({}, MagicMock(), MagicMock(), RoomState(), tool_registry=registry, context_enabled=False), registry


@pytest.mark.asyncio
async def test_delivered_relay_records_own_words_without_source() -> None:
    """送达的补充要求记主播自己的原话，不带附加的来源对话；游戏侧仍收到带来源的版本。"""
    planner, registry = _relay_planner(
        ToolExecutionResult(
            tool_name="framework_prompt",
            success=True,
            structured_content={"delivered": True, "executor": "minecraft"},
        )
    )

    observation = await planner._invoke_registry_tool(
        "framework_prompt",
        {"agent": "minecraft", "content": "机械锯那边别再管了"},
        source_dialogue=[{"role": "user", "content": "观众：锯子修好了吗"}],
    )
    text = await _reference(planner)

    assert json.loads(observation)["delivered"] is True
    assert "[来源对话" in registry.invoke.call_args.args[0].arguments["content"]
    assert "【我最近给手上的事补充过的要求】\n- 刚刚在 minecraft 里补充：机械锯那边别再管了" in text
    assert "来源对话" not in text


@pytest.mark.asyncio
async def test_rejected_or_blocked_relay_is_not_recorded() -> None:
    """拒收的、没有新输入被拦下的补充都没交代到，不能当成说过。"""
    planner, _registry = _relay_planner(
        ToolExecutionResult(tool_name="framework_prompt", success=False, error_message="留言已满")
    )

    await planner._invoke_registry_tool("framework_prompt", {"agent": "minecraft", "content": "先别拆"})
    await planner._invoke_registry_tool(
        "framework_prompt", {"agent": "minecraft", "content": "再说一遍"}, relay_without_input=True
    )

    assert "补充过的要求" not in await _reference(planner)


def test_agent_passes_recall_limits_to_planner() -> None:
    """[agents.streamer] 的回看条数一路传到 Planner。"""
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    agent = StreamerAgent(
        config=StreamerConfig.from_dict(
            {"proactive": {"enabled": False}, "planner_recent_intents_max": 3, "planner_recent_relays_max": 1}
        ),
        llm_manager=MagicMock(),
        prompt_manager=prompt,
    )

    assert agent._planner._recent_intents.maxlen == 3
    assert agent._planner._recent_relays.maxlen == 1
