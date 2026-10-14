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
