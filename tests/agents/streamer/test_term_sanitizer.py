"""注入边界术语脱敏测试：任务号/观测引用/simulated 前缀在进主播上下文前转人类可读。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.streamer_agent import StreamerAgent
from src.agents.streamer.term_sanitizer import sanitize_internal_terms
from src.modules.events.payloads.game import GamePayload
from src.modules.tools.models import ToolExecutionResult, ToolInvocation


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("任务 deleg_1791384781841_1 已交付", "任务 一个游戏任务 已交付"),
        ("证据见 obs_f794817a_fc2e9d31aabbccdd，现场无异常", "证据见 一次现场观察，现场无异常"),
        ("拿到了 simulated:honey_glue", "拿到了 honey_glue"),  # 去前缀但保留物品名
        ("我在 minecraft 里挖到了 19 块圆石，obsidian 还没找到", None),  # 普通文本不动
        ("", ""),
    ],
)
def test_sanitize_internal_terms(text: str, expected) -> None:
    assert sanitize_internal_terms(text) == (text if expected is None else expected)


async def test_game_narrative_line_sanitized_at_boundary() -> None:
    """game.* 叙事进入缓冲前脱敏：任务号不进主播上下文。"""
    agent = StreamerAgent.__new__(StreamerAgent)  # 只测事件行组装，不走完整装配
    agent._game_narrative = MagicMock()
    agent._planner = MagicMock()
    agent._logger = MagicMock()

    payload = GamePayload(
        game="minecraft",
        event_type="report",
        message="任务 deleg_1791384781841_1 完成，蜂蜜胶已放进 simulated:chest",
        report_kind="delivery",
    )
    await agent._on_game_event("game.report", payload, "test")

    line = agent._game_narrative.add.call_args.args[0]
    assert "deleg_" not in line and "simulated:" not in line
    assert "一个游戏任务" in line and "chest" in line


async def test_reference_overview_sanitized() -> None:
    """参考工具的现场速览进参考段前脱敏：观测引用转人类可读。"""

    async def _invoke(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=True,
            content="现场观察 obs_f794817a_fc2e9d31aabbccdd：站在蜂房旁，血量 20",
        )

    registry = MagicMock()
    registry.list_tools = MagicMock(return_value=[])
    registry.invoke = AsyncMock(side_effect=_invoke)
    planner = Planner(
        config={"planner_max_steps": 1, "reference_tools": ["minecraft_glance"]},
        llm_service=MagicMock(),
        prompt_service=MagicMock(),
        room_state=RoomState(),
        tool_registry=registry,
        context_enabled=False,
        reply_provider=MagicMock(),
    )

    overview = await planner._read_reference_tools("r1")

    assert "obs_" not in overview
    assert "一次现场观察" in overview


async def test_delegation_render_without_task_id() -> None:
    """手头的事一栏不再携带任务号，原话中的标识符同样脱敏。"""
    registry = MagicMock()

    async def _invoke(_invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name="framework_delegate",
            success=True,
            structured_content={"accepted": True, "executor": "minecraft", "task_id": "deleg_1"},
        )

    registry.invoke = AsyncMock(side_effect=_invoke)
    planner = Planner({}, MagicMock(), MagicMock(), RoomState(), tool_registry=registry, context_enabled=False)

    await planner._invoke_registry_tool("framework_delegate", {"agent": "minecraft", "instruction": "挖 deleg_9 石料"})
    text = await planner._assemble_reference([], None, None, False, False, "")

    assert "deleg_" not in text
    assert "开始做一个任务：挖 一个游戏任务 石料" in text
