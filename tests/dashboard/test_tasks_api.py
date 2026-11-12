"""任务卡快照 API（GET /api/v1/tasks）：进行中取账本，已完结取事件环终态末次 payload，未装配 503"""

from __future__ import annotations

import pytest

from src.modules.events.event_history import EventHistoryService, EventRecord
from src.modules.events.names import CoreEvents
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger, TaskTracker


@pytest.fixture
def ledger() -> TaskLedger:
    return TaskLedger(event_bus=None)


@pytest.fixture
def history() -> EventHistoryService:
    return EventHistoryService()


@pytest.fixture
def client(mount, initialized_config, ledger: TaskLedger, history: EventHistoryService):
    tracker = TaskTracker(ToolRegistry(), ledger)
    return mount(config_service=initialized_config, task_tracker=tracker, event_history=history)


def _task_changed(task_id: str, status: str, **extra) -> EventRecord:
    return EventRecord(
        type=CoreEvents.TASK_CHANGED,
        event_name=CoreEvents.TASK_CHANGED,
        source="TaskLedger",
        data={"task_id": task_id, "status": status, "timestamp_ms": 1_760_000_000_000, **extra},
    )


def test_running_tasks_come_from_ledger_with_derived_owner(client, ledger: TaskLedger) -> None:
    def register(task_id: str, tool: str, initiator: str, executor: str, status: str, source: str, snapshot: dict):
        provider = "framework" if source == "agent" else executor
        ledger.register(
            task_id=task_id, provider=provider, tool=tool, initiator=initiator, executor=executor,
            status=status, source=source, snapshot=snapshot,
        )  # fmt: skip

    register(
        "deleg_1", "framework_delegate", "operator", "minecraft", "running", "agent", {"instruction": "建一座木头房子"}
    )
    register(
        "deleg_2",
        "framework_delegate",
        "streamer",
        "minecraft",
        "waiting_for_decision",
        "agent",
        {"instruction": "探索东侧"},
    )
    # provider 型自驱执行：executor 是提供者名，归属按发起 Agent 推导
    register("exec_1", "maicraft_execute", "minecraft", "maicraft", "running", "provider", {})

    running = {card["task_id"]: card for card in client.get("/api/v1/tasks").json()["running"]}
    assert set(running) == {"deleg_1", "deleg_2", "exec_1"}
    first = running["deleg_1"]
    assert (first["instruction"], first["status"]) == ("建一座木头房子", "running")
    assert (first["initiator"], first["executor"]) == ("operator", "minecraft")
    assert running["deleg_2"]["status"] == "waiting_for_decision"
    assert {running[t]["owner_agent"] for t in running} == {"minecraft"}  # agent 型=执行 Agent，provider 型=发起 Agent


def test_finished_tasks_come_from_event_history_terminal_states_only(client, history: EventHistoryService) -> None:
    history.record(
        _task_changed(
            "deleg_done",
            "succeeded",
            initiator="operator",
            executor="minecraft",
            source="agent",
            summary="delivery: 完成（deleg_done）",
            snapshot={"instruction": "指令 deleg_done"},
        )
    )
    history.record(_task_changed("deleg_live", "running"))  # 非终态由账本覆盖，不进完结区

    body = client.get("/api/v1/tasks").json()
    finished = {card["task_id"]: card for card in body["finished"]}
    assert set(finished) == {"deleg_done"}
    done = finished["deleg_done"]
    assert (done["summary"], done["instruction"], done["owner_agent"]) == (
        "delivery: 完成（deleg_done）",
        "指令 deleg_done",
        "minecraft",
    )
    assert all(card["task_id"] != "deleg_live" for card in body["running"])


def test_tasks_503_without_tracker(mount, initialized_config) -> None:
    resp = mount(config_service=initialized_config).get("/api/v1/tasks")
    assert resp.status_code == 503 and "任务基建未装配" in resp.json()["detail"]


def test_goal_snapshot_falls_back_to_purpose_for_instruction(
    client, ledger: TaskLedger, history: EventHistoryService
) -> None:
    """游戏目标快照只有 purpose（Mod 契约键）：任务卡指令摘要回退显示 purpose。

    两处投影都验：进行中取账本记录，已完结取事件环末态 payload；
    委派快照 instruction 在场时优先，不受回退影响。
    """
    ledger.register(
        task_id="goal_run_1", provider="maicraft", tool="maicraft_execute",
        initiator="minecraft", executor="maicraft", status="running", source="provider",
        snapshot={"purpose": "烤三块海带"},  # 游戏目标快照无 instruction 键
    )  # fmt: skip
    history.record(
        _task_changed(
            "goal_done",
            "succeeded",
            initiator="minecraft",
            executor="maicraft",
            source="provider",
            summary="goal 完成",
            snapshot={"purpose": "装一扇门"},
        )
    )
    history.record(
        _task_changed(
            "deleg_done",
            "succeeded",
            initiator="operator",
            executor="minecraft",
            source="agent",
            summary="delivery: 完成",
            snapshot={"instruction": "建房子", "purpose": "不该被用到"},
        )
    )

    body = client.get("/api/v1/tasks").json()
    running = {card["task_id"]: card for card in body["running"]}
    finished = {card["task_id"]: card for card in body["finished"]}
    assert running["goal_run_1"]["instruction"] == "烤三块海带"
    assert finished["goal_done"]["instruction"] == "装一扇门"
    assert finished["deleg_done"]["instruction"] == "建房子"


def test_delegation_cards_prefer_own_words_and_distinguish_queued_and_initiator_closed(
    client, ledger: TaskLedger, history: EventHistoryService
) -> None:
    """委派卡片优先展示 own_words（不把附带来源对话整段搬上看板），待办区分未开工与搁下，主播结单进已完结区。"""
    ledger.register(
        task_id="deleg_q1",
        provider="framework",
        tool="framework_delegate",
        initiator="planner-react",
        executor="minecraft",
        status="queued",
        source="agent",
        snapshot={"instruction": "[本次目标]\n开一块菜地\n\n[来源对话：…]", "own_words": "开一块菜地"},
    )
    ledger.register(
        task_id="deleg_q2",
        provider="framework",
        tool="framework_delegate",
        initiator="planner-react",
        executor="minecraft",
        status="queued",
        source="agent",
        snapshot={"own_words": "建木屋", "shelved_at_ms": 1_760_000_000_000, "shelved_progress": "地基铺好了"},
    )
    history.record(
        _task_changed(
            "deleg_closed",
            "closed_by_initiator",
            initiator="planner-react",
            executor="minecraft",
            source="agent",
            summary="发起方认定已完成：四格树苗已栽满",
            snapshot={"own_words": "栽四棵树苗"},
        )
    )

    body = client.get("/api/v1/tasks").json()
    running = {card["task_id"]: card for card in body["running"]}
    finished = {card["task_id"]: card for card in body["finished"]}
    assert running["deleg_q1"]["instruction"] == "开一块菜地"
    assert running["deleg_q1"]["summary"] == "还没开工"
    assert running["deleg_q2"]["summary"] == "搁下了，做到：地基铺好了"
    assert finished["deleg_closed"]["status"] == "closed_by_initiator"
    assert finished["deleg_closed"]["instruction"] == "栽四棵树苗"
