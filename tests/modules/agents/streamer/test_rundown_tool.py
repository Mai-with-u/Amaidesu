"""RundownControlProvider 单元测试

验证 Agent 内脏协议工具的调用契约：结构化拒绝回灌（unknown id / 未达
最少停留）、成功快照、参数缺失与未知动作的防御；以及经 ToolRegistry
注册后的统一调用路径（可见名单 / Planner 分派 / 状态机变更落同一处）。
"""

from __future__ import annotations

import json
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.rundown.rundown_state import RundownState
from src.agents.streamer.tools.rundown_tool import (
    RundownControlProvider,
    build_rundown_tool_provider,
)
from src.modules.llm.payload import Response as PayloadResponse
from src.modules.llm.payload import ToolCall as PayloadToolCall
from src.modules.storage.models.rundown import DEFAULT_RUNDOWN, Rundown, RundownSegment
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry


class _FakeClock:
    """可手动拨动的假时钟（毫秒）。"""

    def __init__(self, start: int = 1_000_000) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


def _make_rundown_with_min() -> Rundown:
    """首段带 min_duration 守卫的三段流程单（默认流程单不含 min）。"""
    return Rundown(
        rundown_id="tool_rd",
        title="工具测试流程单",
        segments=[
            RundownSegment(
                id="opening",
                title="开场",
                task_description="开场目标",
                expected_ms=600_000,
                min_duration_ms=60_000,
            ),
            RundownSegment(id="chat", title="闲聊", task_description="闲聊目标", expected_ms=600_000),
            RundownSegment(id="closing", title="收尾", task_description="收尾目标", expected_ms=300_000),
        ],
    )


def _make_provider(clock: _FakeClock, rundown: Rundown) -> RundownControlProvider:
    state = RundownState(clock=clock)
    state.load(rundown, now_ms=clock.now)
    return RundownControlProvider(state)


def _parse(text: str) -> Dict[str, Any]:
    return json.loads(text)


def _register_rundown_tool(registry: ToolRegistry, provider: RundownControlProvider) -> None:
    """按生产口径把 rundown_control 注册进 registry（名单 ["streamer"]）。"""
    registry.register_provider(
        build_rundown_tool_provider(provider),
        visible_to={"rundown_control": ["streamer"]},
    )


def test_visible_to_streamer_with_full_function_shape() -> None:
    """注册进 registry 后 Planner 工具列表含 rundown_control（全名直出，形状完整）。"""
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(DEFAULT_RUNDOWN, now_ms=clock.now)
    provider = RundownControlProvider(state)

    registry = ToolRegistry()
    _register_rundown_tool(registry, provider)
    planner = Planner(
        config={"planner_max_steps": 4},
        llm_service=MagicMock(),
        prompt_service=MagicMock(),
        room_state=RoomState(),
        tool_registry=registry,
    )

    fn_defs = {f["name"]: f for f in planner._build_tool_list()}

    assert "rundown_control" in fn_defs
    props = fn_defs["rundown_control"]["parameters"]["properties"]
    assert props["action"]["enum"] == ["next", "goto", "pause", "resume"]
    assert fn_defs["rundown_control"]["parameters"]["required"] == ["action"]


@pytest.mark.asyncio
async def test_registry_invoke_advances_state_machine() -> None:
    """回归：经 registry.invoke 调 rundown_control，状态机变更落同一处。"""
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(DEFAULT_RUNDOWN, now_ms=clock.now)

    registry = ToolRegistry()
    _register_rundown_tool(registry, RundownControlProvider(state))

    result = await registry.invoke(
        ToolInvocation(tool_name="rundown_control", arguments={"action": "next"}, source="planner-react")
    )

    assert result.success is True
    payload = result.structured_content
    assert payload["ok"] is True
    assert payload["rundown"]["current"]["id"] == "self_intro"
    assert state.get_snapshot()["current"]["id"] == "self_intro"


@pytest.mark.asyncio
async def test_planner_dispatches_rundown_control_via_registry() -> None:
    """回归：Planner ReAct 循环调 rundown_control 走 registry 单一路径，观察完整回灌。

    与改造前直连行为一致：观察含流程单快照（非退化占位），状态机推进由
    同一 RundownState 承载。
    """
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(DEFAULT_RUNDOWN, now_ms=clock.now)

    registry = ToolRegistry()
    _register_rundown_tool(registry, RundownControlProvider(state))

    llm = MagicMock()
    llm.generate = AsyncMock(
        side_effect=[
            PayloadResponse(
                success=True,
                tool_calls=[PayloadToolCall(id="c1", name="rundown_control", arguments={"action": "next"})],
            ),
            PayloadResponse(success=True, content="本轮到此"),
        ]
    )
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM_PROMPT")

    planner = Planner(
        config={"planner_max_steps": 4},
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
    )

    outcome = await planner.plan([])

    assert outcome["replied"] is False
    assert outcome["tool_trace"] == ["rundown_control"]
    assert outcome["silent_reason"] == "natural"
    # 状态机变更落同一处：next 已推进到开场后的环节
    assert state.get_snapshot()["current"]["id"] == "self_intro"
    # 观察完整回灌：第二轮 messages 的 tool 观察含快照（非 {"ok": true} 退化占位）
    second = llm.generate.await_args_list[1].args[0]
    tool_msgs = [m for m in second if m.get("role") == "tool"]
    assert tool_msgs and '"rundown"' in tool_msgs[0]["content"]
    assert '"current"' in tool_msgs[0]["content"]


def test_invoke_next_success_returns_snapshot() -> None:
    clock = _FakeClock()
    provider = _make_provider(clock, DEFAULT_RUNDOWN)

    result = _parse(provider.invoke({"action": "next"}))

    assert result["ok"] is True
    assert result["rundown"]["status"] == "running"
    assert result["rundown"]["current"]["id"] == "self_intro"


def test_invoke_next_min_duration_rejected_with_remaining() -> None:
    clock = _FakeClock()
    provider = _make_provider(clock, _make_rundown_with_min())

    result = _parse(provider.invoke({"action": "next"}))

    assert result["ok"] is False
    assert result["reason"] == "min_duration_not_met"
    assert result["remaining_ms"] == 60_000


def test_invoke_goto_requires_segment_id() -> None:
    clock = _FakeClock()
    provider = _make_provider(clock, DEFAULT_RUNDOWN)

    result = _parse(provider.invoke({"action": "goto"}))

    assert result["ok"] is False
    assert "segment_id" in result["error"]


def test_invoke_goto_unknown_id_returns_available_ids() -> None:
    clock = _FakeClock()
    provider = _make_provider(clock, DEFAULT_RUNDOWN)

    result = _parse(provider.invoke({"action": "goto", "segment_id": "nope"}))

    assert result["ok"] is False
    assert result["reason"] == "unknown_segment_id"
    assert result["available_segment_ids"] == ["opening", "self_intro", "chat", "closing"]


def test_invoke_pause_and_resume() -> None:
    clock = _FakeClock()
    provider = _make_provider(clock, DEFAULT_RUNDOWN)

    paused = _parse(provider.invoke({"action": "pause"}))
    assert paused["ok"] is True
    assert paused["rundown"]["status"] == "paused"

    resumed = _parse(provider.invoke({"action": "resume"}))
    assert resumed["ok"] is True
    assert resumed["rundown"]["status"] == "running"


def test_invoke_unknown_action_rejected() -> None:
    clock = _FakeClock()
    provider = _make_provider(clock, DEFAULT_RUNDOWN)

    result = _parse(provider.invoke({"action": "teleport"}))

    assert result["ok"] is False
    assert "teleport" in result["error"]


def test_invoke_finish_marks_done() -> None:
    clock = _FakeClock()
    provider = _make_provider(clock, DEFAULT_RUNDOWN)
    for _ in range(3):
        provider.invoke({"action": "next"})

    result = _parse(provider.invoke({"action": "next"}))

    assert result["ok"] is True
    assert result["rundown"]["status"] == "done"
