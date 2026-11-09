"""委派队列在主播侧的镜像与视图：排队回执进镜像、送达挪进手头的事、【委派队列】渲染、挂起状态行。

队列本体在委派受理核心（src/modules/agents/control.py）；工作台只持镜像，
按任务账本对账（``settle_queue``）。账本用 ``statuses`` 字典替身：任务号 → 账面。
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

from src.agents.streamer.delegation_workbench import DelegationWorkbench
from src.agents.streamer.planner_render import (
    Delegation,
    QueuedDelegation,
    delegation_state_line,
    render_delegation_queue,
)
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.tools.models import ToolExecutionResult, ToolInvocation


def _workbench(statuses: Dict[str, Dict[str, Any]], receipts: List[Dict[str, Any]]) -> DelegationWorkbench:
    """按顺序回放受理回执的工作台；账本查询读 ``statuses``。"""
    pending = list(receipts)

    async def _invoke(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, structured_content=pending.pop(0))

    registry = MagicMock()
    registry.invoke = AsyncMock(side_effect=_invoke)
    return DelegationWorkbench(registry, intents_max=4, relays_max=4, delegation_status_provider=statuses.get)


async def test_queued_receipt_goes_to_mirror_without_replacing_current_work() -> None:
    """目标忙时的排队回执进排队镜像，不替换手头正在做的那件。"""
    statuses: Dict[str, Dict[str, Any]] = {
        "deleg_1": {"status": "running", "updated_at_ms": 1},
        "deleg_2": {"status": "queued", "updated_at_ms": 1},
    }
    workbench = _workbench(
        statuses,
        [
            {"accepted": True, "task_id": "deleg_1", "executor": "minecraft"},
            {"accepted": True, "task_id": "deleg_2", "executor": "minecraft", "queued": True, "queue_position": 1},
        ],
    )
    await workbench.handle("framework_delegate", {"agent": "minecraft", "instruction": "盖房子"})
    await workbench.handle("framework_delegate", {"agent": "minecraft", "instruction": "浇菜地", "urgent": False})

    assert workbench.delegations["minecraft"].task_id == "deleg_1", "手头的事不被排队件替换"
    queued = workbench.queued_delegations["minecraft"]
    assert [(entry.task_id, entry.instruction, entry.urgent) for entry in queued] == [("deleg_2", "浇菜地", False)]


async def test_settle_queue_promotes_delivered_and_drops_settled() -> None:
    """对账：送达开跑的挪进手头的事，已了结（查不到）的移出，还排着的留着。"""
    statuses: Dict[str, Dict[str, Any]] = {
        "deleg_1": {"status": "running", "updated_at_ms": 1},
        "deleg_2": {"status": "queued", "updated_at_ms": 1},
        "deleg_3": {"status": "queued", "updated_at_ms": 1},
        "deleg_4": {"status": "queued", "updated_at_ms": 1},
    }
    workbench = _workbench(
        statuses,
        [
            {"accepted": True, "task_id": "deleg_1", "executor": "minecraft"},
            {"accepted": True, "task_id": "deleg_2", "executor": "minecraft", "queued": True},
            {"accepted": True, "task_id": "deleg_3", "executor": "minecraft", "queued": True},
            {"accepted": True, "task_id": "deleg_4", "executor": "minecraft", "queued": True},
        ],
    )
    for instruction in ("盖房子", "浇菜地", "砍树", "钓鱼"):
        await workbench.handle("framework_delegate", {"agent": "minecraft", "instruction": instruction})

    # 盖房子做完（终态移除）→ 浇菜地送达开跑；砍树被撤（终态移除）；钓鱼还排着
    del statuses["deleg_1"]
    statuses["deleg_2"] = {"status": "accepted", "updated_at_ms": 2}
    del statuses["deleg_3"]
    workbench.settle_queue()

    assert workbench.delegations["minecraft"].task_id == "deleg_2"
    assert workbench.delegations["minecraft"].instruction == "浇菜地"
    assert [entry.task_id for entry in workbench.queued_delegations["minecraft"]] == ["deleg_4"]

    # 最后一件也了结：执行者的镜像整条清掉
    del statuses["deleg_4"]
    workbench.settle_queue()
    assert "minecraft" not in workbench.queued_delegations


async def test_reset_clears_queue_mirror() -> None:
    """开播清场：排队镜像整个清掉（下播时排着的单已被强制结算成 cancelled）。"""
    statuses: Dict[str, Dict[str, Any]] = {"deleg_2": {"status": "queued", "updated_at_ms": 1}}
    workbench = _workbench(
        statuses, [{"accepted": True, "task_id": "deleg_2", "executor": "minecraft", "queued": True}]
    )
    await workbench.handle("framework_delegate", {"agent": "minecraft", "instruction": "浇菜地"})
    workbench.reset()
    assert workbench.queued_delegations == {}


def test_render_delegation_queue_one_line_per_entry() -> None:
    """【委派队列】一件一行：多久前、排给谁、第几件、原话（脱敏），急件标出插队。"""
    current_ms = 10 * 60 * 1000
    queued = {
        "minecraft": [
            QueuedDelegation(agent="minecraft", instruction="救火", task_id="deleg_9", at_ms=current_ms, urgent=True),
            QueuedDelegation(agent="minecraft", instruction="浇菜地", task_id="deleg_8", at_ms=current_ms - 120_000),
        ]
    }
    text = render_delegation_queue(queued, current_ms)
    lines = text.split("\n")
    assert len(lines) == 2
    assert "急件，插队给 minecraft（第 1 件）：救火" in lines[0]
    assert "排队给 minecraft（第 2 件）：浇菜地" in lines[1] and "轮到时自动送达" in lines[1]
    assert "deleg_" not in text, "任务号不进视图（会被念给观众）"
    assert render_delegation_queue({}, current_ms) == ""


def test_suspended_delegation_state_line() -> None:
    """被急件暂放的在途件：状态行写明挂起、进度保留、急事办完接着做。"""
    delegation = Delegation(agent="minecraft", instruction="盖房子", task_id="deleg_1", at_ms=0)
    statuses = {"deleg_1": {"status": "suspended", "updated_at_ms": 0}}
    line = delegation_state_line(delegation, statuses.get, 60_000)
    assert "挂起" in line and "急事办完接着做" in line


def test_delegate_rules_state_queue_and_obedience_table() -> None:
    """委派守则：忙时普通件自动排队、不重复派；急件听从权衡表四档；战斗受伤逃生不归这套；急件不打断急件。"""
    reset_prompt_manager()
    try:
        rules = get_prompt_manager().render("amaidesu_planner_delegate_rules")
    finally:
        reset_prompt_manager()
    assert "普通件自动排队" in rules and "不要重复派同一件事" in rules and "【委派队列】" in rules
    assert "`urgent` 设为 true" in rules
    assert "SC、舰长的附带请求默认遵从" in rules
    assert "普通弹幕点名归你权衡" in rules
    assert "矿洞深处被叫上地面" in rules and "委婉拒绝并说明原因" in rules
    assert "付费过的观众代价让位" in rules
    assert "战斗、受伤、逃生**不归这套**" in rules and "不要为此发急件" in rules
    assert "同一时刻最多一件急事在办" in rules
