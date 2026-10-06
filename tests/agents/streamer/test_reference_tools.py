"""决策前自动读取配置的只读参考工具，现场速览直接进参考段，省掉“先查一步再开口”的请求。

实测主播决策里四成请求只调用了看一眼游戏的工具，看完下一步才决定说什么。
"""

from __future__ import annotations

from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import RoomState
from src.modules.llm.payload import Response
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.tools.models import ToolExecutionResult, ToolInvocation


def _planner(reference_tools: List[str], invoke: Any) -> tuple[Planner, List[List[dict]]]:
    captured: List[List[dict]] = []

    async def _generate(messages: List[dict], **_: Any) -> Response:
        captured.append([dict(m) for m in messages])
        return Response(success=True, content="不说")

    registry = MagicMock()
    registry.list_tools = MagicMock(return_value=[])
    registry.invoke = AsyncMock(side_effect=invoke)
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=_generate)
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    planner = Planner(
        config={"planner_max_steps": 2, "reference_tools": reference_tools},
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
        reply_provider=MagicMock(),
    )
    return planner, captured


@pytest.mark.asyncio
async def test_configured_reference_tools_feed_the_reference_block() -> None:
    """配置的参考工具在第一次推理前调用一次，结果作为【现场速览】进参考段。"""
    calls: List[ToolInvocation] = []

    async def _invoke(invocation: ToolInvocation) -> ToolExecutionResult:
        calls.append(invocation)
        return ToolExecutionResult(
            tool_name=invocation.tool_name, success=True, structured_content={"position": "蜂房旁", "health": 20}
        )

    planner, captured = _planner(["minecraft_glance"], _invoke)
    await planner.plan([], proactive=True, trigger_reason="proactive:cold")
    assert [(c.tool_name, c.arguments, c.source) for c in calls] == [("minecraft_glance", {}, "planner-reference")]
    reference = captured[0][-1]["content"]
    assert "【现场速览（本轮决策前自动读取）】" in reference and "蜂房旁" in reference


@pytest.mark.asyncio
async def test_failed_reference_tool_is_reported_without_blocking() -> None:
    """参考工具失败时照实写出原因，决策照常进行；未配置时不调用任何工具。"""

    async def _fail(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=invocation.tool_name, success=False, error_message="游戏未连接")

    planner, captured = _planner(["minecraft_glance"], _fail)
    await planner.plan([], proactive=True, trigger_reason="proactive:cold")
    assert "minecraft_glance: 读取失败（游戏未连接）" in captured[0][-1]["content"]

    calls: List[ToolInvocation] = []

    async def _record(invocation: ToolInvocation) -> ToolExecutionResult:
        calls.append(invocation)
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, structured_content={})

    planner, captured = _planner([], _record)
    await planner.plan([], proactive=True, trigger_reason="proactive:cold")
    assert calls == [] and "【现场速览" not in captured[0][-1]["content"]


def test_streamer_config_defaults_to_no_reference_tools() -> None:
    """配置默认不调用任何参考工具；接哪个游戏就在配置里填哪个游戏的只读工具。"""
    assert StreamerConfig.from_dict({}).reference_tools == []
    assert StreamerConfig.from_dict({"reference_tools": ["minecraft_glance"]}).reference_tools == ["minecraft_glance"]


def test_planner_prompt_mentions_overview() -> None:
    reset_prompt_manager()
    try:
        prompt = get_prompt_manager().render("amaidesu_planner_react", behavior_style="积极互动")
    finally:
        reset_prompt_manager()
    assert "【现场速览】是本轮决策前自动读取的现场" in prompt
