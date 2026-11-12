"""委派停滞告警的决断兜底：告警进决策窗材料、决断计数、第 N 次告警自动撤单。

构成：``DelegationStallWatch`` 直测（计数 / 决断判定 / 状态映射，注入假时钟）+
StreamerAgent 挂接测（``on_task_notification`` 把材料记进游戏账本、置位待定夺信
号、撤单经 ``framework_task_settle`` 发出）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

from src.agents.streamer.delegation_stall_watch import DelegationStallWatch
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.tools.models import ToolExecutionResult, ToolInvocation

from tests.agents.streamer.helpers import make_streamer_agent


def _alert(
    task_id: str = "deleg_9",
    status: str = "queued",
    snapshot: Optional[Dict[str, Any]] = None,
    timestamp_ms: int = 0,
) -> TaskChangedPayload:
    return TaskChangedPayload(
        task_id=task_id,
        status=status,
        summary="已 1800s 无进展",
        initiator="streamer",
        executor="minecraft",
        source="agent",
        snapshot={"instruction": "去栽一批树苗", "own_words": "帮我栽一批树苗", "queued_at_ms": 1}
        if snapshot is None
        else snapshot,
        timestamp_ms=timestamp_ms,
        alert=True,
    )


def _watch(threshold: int = 3, now_ms_value: int = 0) -> tuple[DelegationStallWatch, List[int]]:
    ticks = [now_ms_value]

    def clock() -> int:
        return ticks[0]

    return DelegationStallWatch(auto_cancel_alerts=threshold, clock=clock), ticks


def test_first_alert_builds_material_with_instruction_and_stopped_time() -> None:
    watch, ticks = _watch(now_ms_value=1_800_000)
    outcome = watch.note_alert(_alert(timestamp_ms=1_800_000))

    assert outcome is not None and not outcome.auto_cancel
    assert outcome.executor == "minecraft" and outcome.own_words == "帮我栽一批树苗"
    assert "帮我栽一批树苗" in outcome.material and "首次" in outcome.material
    # 停了多久 = 自上次进展（这里是首次观测）起的分钟数
    ticks[0] = 1_800_000 + 120_000
    outcome = watch.note_alert(_alert(timestamp_ms=1_920_000))
    assert outcome is not None and "2 分钟" in outcome.material and "第 2 次" in outcome.material


def test_third_alert_on_queued_delegation_decides_auto_cancel() -> None:
    watch, _ = _watch(threshold=3)
    watch.note_alert(_alert())
    watch.note_alert(_alert())
    outcome = watch.note_alert(_alert())
    assert outcome is not None and outcome.auto_cancel, "排队件第 3 次告警仍无决断 → 自动撤单"

    # 决断兜底不杀运行中的任务：running 状态怎么告警都不判撤单
    watch_running, _ = _watch(threshold=3)
    for _ in range(4):
        outcome = watch_running.note_alert(_alert(status="running"))
    assert outcome is not None and not outcome.auto_cancel


def test_progress_between_alerts_resets_the_count() -> None:
    watch, _ = _watch(threshold=3)
    watch.note_alert(_alert())
    watch.note_alert(_alert())
    # 两次告警之间账面真变化（queued → accepted，快照变了）：算有进展，重新计数
    changed = TaskChangedPayload(
        task_id="deleg_9",
        status="accepted",
        summary="轮到执行",
        initiator="streamer",
        executor="minecraft",
        source="agent",
        snapshot={"instruction": "去栽一批树苗", "own_words": "帮我栽一批树苗"},
        timestamp_ms=5,
    )
    watch.note_change(changed)
    outcome = watch.note_alert(
        _alert(status="accepted", snapshot={"instruction": "去栽一批树苗", "own_words": "帮我栽一批树苗"})
    )
    assert outcome is not None and not outcome.auto_cancel, "变化后计数重置，第 1 次不撤单"

    # 旁注与受理原文不算进展：快照只有这些键变化时计数照常累计（已数到 1，再两轮到阈值）
    annotated = dict(_alert().snapshot or {})
    annotated["latest_reply"] = "我觉得快好了"
    watch.note_alert(_alert(snapshot=annotated))
    outcome = watch.note_alert(_alert(snapshot=annotated))
    assert outcome is not None and not outcome.auto_cancel
    outcome = watch.note_alert(_alert(snapshot=annotated))
    assert outcome is not None and outcome.auto_cancel, "旁注不算决断，第 3 次照判撤单"


def test_terminal_change_drops_the_entry() -> None:
    watch, _ = _watch(threshold=2)
    watch.note_alert(_alert())
    ended = TaskChangedPayload(
        task_id="deleg_9",
        status="cancelled",
        summary="已撤",
        initiator="streamer",
        executor="minecraft",
        source="agent",
        snapshot={},
        timestamp_ms=9,
    )
    watch.note_change(ended)
    outcome = watch.note_alert(_alert())
    assert outcome is not None and not outcome.auto_cancel, "已终态的任务重新从首次数起"


async def test_streamer_routes_alerts_into_decision_material_and_cancels_on_threshold() -> None:
    """挂接测：告警记进游戏账本必读材料、置位待定夺信号；到阈值的排队件经 framework_task_settle 撤掉。"""
    invokes: List[ToolInvocation] = []

    async def _invoke(invocation: ToolInvocation) -> ToolExecutionResult:
        invokes.append(invocation)
        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=True,
            structured_content={"settled": True, "outcome": "drop", "status": "cancelled"},
        )

    registry = MagicMock()
    registry.invoke = AsyncMock(side_effect=_invoke)
    agent = make_streamer_agent(tool_registry=registry)
    try:
        agent.on_task_notification(_alert())
        assert agent._proactive_signals.game_pending is True, "告警置位待定夺信号唤醒决策"
        lines = agent._game_ledger.pending_lines()
        assert any("task_alert" in line and "帮我栽一批树苗" in line for line in lines), "告警进决策窗必读材料"
        assert not invokes, "未到阈值不撤单"

        agent.on_task_notification(_alert())
        agent.on_task_notification(_alert())
        await asyncio.sleep(0.05)
        assert len(invokes) == 1
        assert invokes[0].tool_name == "framework_task_settle" and invokes[0].arguments == {
            "item": "deleg_9",
            "outcome": "drop",
        }
        assert any("auto_cancel" in line and "喊停" in line for line in agent._game_ledger.pending_lines()), (
            "撤单后补一条场记事实，主播下一窗顺带交代"
        )

        # 非告警的终态变化：对账出册，不再重复撤单
        ended = TaskChangedPayload(
            task_id="deleg_9",
            status="cancelled",
            summary="已撤",
            initiator="streamer",
            executor="minecraft",
            source="agent",
            snapshot={},
            timestamp_ms=99,
        )
        agent.on_task_notification(ended)
        outcome = agent._stall_watch.note_alert(_alert())
        assert outcome is not None and not outcome.auto_cancel
        assert len(invokes) == 1, "终态出册后不重复撤单"
    finally:
        await agent.stop()
