"""RundownControlProvider：Agent 内脏协议工具的调用契约（结构化拒绝回灌、成功快照、参数缺失与未知动作防御）

以及经 ToolRegistry 注册后的统一调用路径：可见名单 / Planner 分派 / 状态机变更落同一处。
"""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest
from unittest.mock import AsyncMock, MagicMock


from src.agents.streamer.planner import PlannerConfig, Planner
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.rundown.rundown_state import RundownState
from src.agents.streamer.tools.rundown_tool import (
    _PARAMETERS_SCHEMA,
    RundownControlProvider,
    build_rundown_tool_provider,
)
from src.modules.llm.payload import Response, ToolCall
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


def _rundown_with_min_duration() -> Rundown:
    """首段带 min_duration 守卫的三段流程单（默认流程单不含 min）。"""
    return Rundown(
        rundown_id="tool_rd",
        title="工具测试流程单",
        segments=[
            RundownSegment(
                id="opening", title="开场", task_description="开场目标", expected_ms=600_000, min_duration_ms=60_000
            ),
            RundownSegment(id="chat", title="闲聊", task_description="闲聊目标", expected_ms=600_000),
            RundownSegment(id="closing", title="收尾", task_description="收尾目标", expected_ms=300_000),
        ],
    )


def _provider(rundown: Rundown = DEFAULT_RUNDOWN) -> tuple[RundownControlProvider, _FakeClock]:
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(rundown, now_ms=clock.now)
    state.begin(by="system", now_ms=clock.now)  # 工具用例需要 running 语义：load 后显式起算
    return RundownControlProvider(state), clock


def _call(provider: RundownControlProvider, **arguments: Any) -> Dict[str, Any]:
    return json.loads(provider.invoke(arguments))


def _registry_with(provider: RundownControlProvider) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_provider(build_rundown_tool_provider(provider), visible_to={"rundown_control": ["streamer"]})
    return registry


def _planner(registry: ToolRegistry, llm: Any = None) -> Planner:
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM_PROMPT")
    return Planner(
        config=PlannerConfig(planner_max_steps=4),
        llm_service=llm or MagicMock(),
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
    )


def test_tool_is_visible_to_the_planner_with_a_complete_function_shape() -> None:
    provider, _ = _provider()
    fn_defs = {f["name"]: f for f in _planner(_registry_with(provider))._build_tool_list()}
    parameters = fn_defs["rundown_control"]["parameters"]
    assert parameters["properties"]["action"]["enum"] == ["next", "goto", "pause", "resume", "renew", "append"]
    assert parameters["required"] == ["action"]
    assert "renew" in _PARAMETERS_SCHEMA["properties"]["action"]["enum"]  # Planner 侧可见续期能力
    assert "restart" not in _PARAMETERS_SCHEMA["properties"]["action"]["enum"]  # 整场重开是导演通道，Agent 无权
    seg_schema = _PARAMETERS_SCHEMA["properties"]["segment"]
    assert seg_schema["type"] == "object"
    assert set(seg_schema["required"]) == {"seg_id", "title", "task_description", "expected_ms"}


async def test_registry_invoke_and_planner_dispatch_share_one_state_machine() -> None:
    provider, _ = _provider()
    registry = _registry_with(provider)
    result = await registry.invoke(
        ToolInvocation(tool_name="rundown_control", arguments={"action": "next"}, source="planner-react")
    )
    assert result.success is True and result.structured_content["ok"] is True
    assert result.structured_content["rundown"]["current"]["id"] == "self_intro"

    other, _ = _provider()
    llm = MagicMock()
    llm.generate = AsyncMock(
        side_effect=[
            Response(
                success=True, tool_calls=[ToolCall(id="c1", name="rundown_control", arguments={"action": "next"})]
            ),
            Response(success=True, content="本轮到此"),
        ]
    )
    outcome = await _planner(_registry_with(other), llm).plan([])

    assert (outcome.replied, outcome.tool_trace, outcome.silent_reason) == (
        False,
        ["rundown_control"],
        "natural",
    )
    assert other._state.get_snapshot()["current"]["id"] == "self_intro"  # 状态机变更落同一处
    second_round = llm.generate.await_args_list[1].args[0]
    observation = next(m for m in second_round if m.get("role") == "tool")["content"]
    assert '"rundown"' in observation and '"current"' in observation  # 观察完整回灌，非 {"ok": true} 退化占位


def test_next_goto_pause_resume_and_finish() -> None:
    provider, _ = _provider()
    first = _call(provider, action="next")
    assert (
        first["ok"] is True
        and first["rundown"]["status"] == "running"
        and first["rundown"]["current"]["id"] == "self_intro"
    )

    assert _call(provider, action="pause")["rundown"]["status"] == "paused"
    assert _call(provider, action="resume")["rundown"]["status"] == "running"

    for _ in range(2):
        _call(provider, action="next")
    finished = _call(provider, action="next")
    assert finished["ok"] is True and finished["rundown"]["status"] == "done"


def test_structured_rejections_are_fed_back_instead_of_raising() -> None:
    provider, _ = _provider(_rundown_with_min_duration())
    guarded = _call(provider, action="next")
    assert (guarded["ok"], guarded["reason"], guarded["remaining_ms"]) == (False, "min_duration_not_met", 60_000)

    plain, _ = _provider()
    assert "segment_id" in _call(plain, action="goto")["error"]  # 参数缺失
    unknown = _call(plain, action="goto", segment_id="nope")
    assert unknown["reason"] == "unknown_segment_id"
    assert unknown["available_segment_ids"] == ["opening", "self_intro", "chat", "closing"]
    teleport = _call(plain, action="teleport")
    assert teleport["ok"] is False and "teleport" in teleport["error"]


def test_renew_resets_the_segment_timer_and_is_rejected_after_completion() -> None:
    provider, clock = _provider()
    first = DEFAULT_RUNDOWN.segments[0]
    clock.advance(first.expected_ms + 60_000)
    assert provider._state.get_current_remaining_ms(now_ms=clock.now) == 0

    renewed = _call(provider, action="renew")
    current = renewed["rundown"]["current"]
    assert renewed["ok"] is True and renewed["rundown"]["status"] == "running"
    assert (current["remaining_ms"], current["elapsed_ms"]) == (first.expected_ms, 0)  # 剩余时长回到完整周期

    for _ in range(len(DEFAULT_RUNDOWN.segments)):
        _call(provider, action="next")
    done = _call(provider, action="renew")
    assert (done["ok"], done["reason"]) == (False, "already_done")


def _rundown_with_blank_title() -> Rundown:
    """中段标题全空白的流程单（编辑器漏填的脏数据形态）。

    标题非空校验已下沉模型层，构造后原地改脏以模拟绕过模型校验的鸭子实例
    （如历史存量数据）——状态机守卫保留为纵深防御，正是拦这类形态。
    """
    rundown = Rundown(
        rundown_id="blank_rd",
        title="空标题流程单",
        segments=[
            RundownSegment(id="a", title="开场", task_description="目标", expected_ms=60_000),
            RundownSegment(id="b", title="闲聊", task_description="目标", expected_ms=60_000),
        ],
    )
    rundown.segments[1].title = "   "
    return rundown


@pytest.mark.parametrize("action", ["next", "goto"])
def test_invoke_blank_title_target_rejected(action: str) -> None:
    """切换到标题为空/全空白的目标环节被状态机拒绝（不发 rundown.changed）。

    门禁收敛在 ``RundownState``（唯一变更边界），工具只透传拒绝原因码——
    Dashboard 手动通路同受此拦，不再有只有 Agent 被拦的分叉。
    """
    provider, _clock = _provider(_rundown_with_blank_title())
    emitted: list[Any] = []
    provider._state._emit = lambda name, payload: emitted.append(payload)

    result = _call(provider, action=action, segment_id="b")

    assert result["ok"] is False
    assert result["reason"] == "blank_title_target"
    assert emitted == []
    assert provider._state.get_snapshot()["current"]["id"] == "a"


# ---------------------------------------------------------------------------
# append（第六动作）
# ---------------------------------------------------------------------------


def test_append_appends_segment_and_returns_snapshot() -> None:
    provider, clock = _provider()
    result = _call(
        provider,
        action="append",
        segment={
            "seg_id": "bonus",
            "title": "临时加场",
            "task_description": "回应弹幕热点",
            "expected_ms": 120_000,
        },
    )
    assert result["ok"] is True and result["rundown"]["status"] == "running"
    assert result["rundown"]["total"] == 5
    # running 态游标不动，新环节进 upcoming
    assert result["rundown"]["current"]["id"] == "opening"
    appended = provider._state.rundown.segments[-1]
    assert (appended.id, appended.title, appended.expected_ms) == ("bonus", "临时加场", 120_000)
    assert clock is not None


def test_append_after_done_extends_lifecycle() -> None:
    provider, _ = _provider()
    for _ in range(4):
        _call(provider, action="next")
    assert provider._state.get_snapshot()["status"] == "done"
    result = _call(
        provider,
        action="append",
        segment={"seg_id": "encore", "title": "返场", "task_description": "自由闲聊", "expected_ms": 300_000},
    )
    # 续命：追加后 status 回 running，游标指到新环节
    assert result["ok"] is True
    assert (result["rundown"]["status"], result["rundown"]["current"]["id"]) == ("running", "encore")


def test_append_expected_ms_lower_bound_rejected() -> None:
    provider, _ = _provider()
    result = _call(
        provider,
        action="append",
        segment={"seg_id": "tiny", "title": "太短", "task_description": "x", "expected_ms": 30_000},
    )
    assert result["ok"] is False
    assert "至少 1 分钟" in result["error"]
    assert len(provider._state.rundown.segments) == 4  # 拒绝不变更


def test_append_missing_or_malformed_segment_rejected() -> None:
    provider, _ = _provider()
    missing = _call(provider, action="append")
    assert missing["ok"] is False and "segment" in missing["error"]

    bad = _call(
        provider,
        action="append",
        segment={"seg_id": "", "title": "  ", "task_description": "x", "expected_ms": 120_000},
    )
    assert bad["ok"] is False and "校验失败" in bad["error"]

    dup = _call(
        provider,
        action="append",
        segment={"seg_id": "opening", "title": "撞 id", "task_description": "x", "expected_ms": 120_000},
    )
    assert dup["ok"] is False and dup["reason"] == "duplicate_segment_id"
    assert "opening" in dup["available_segment_ids"]
