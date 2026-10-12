"""任务卡快照 API 测试套件（GET /api/v1/tasks）

覆盖：
1. 进行中 = 任务记录表账面（含指令摘要 / 状态 / 发起方→执行方）
2. 已完结 = 事件环聚合的终态末次 payload（仅本次运行内）；非终态事件不重复出现
3. task_tracker 未装配 → 503
"""

from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from src.modules.events.event_history import EventHistoryService, EventRecord
from src.modules.events.names import CoreEvents
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger, TaskTracker


@pytest.fixture
def client(tmp_path: Path) -> Generator[tuple[TestClient, TaskLedger, EventHistoryService], None, None]:
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.config.service import ConfigService
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    svc = ConfigService(base_dir=str(tmp_path))
    svc.initialize()
    ledger = TaskLedger(event_bus=None)
    tracker = TaskTracker(ToolRegistry(), ledger)
    history = EventHistoryService()
    server = DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60215),
        task_tracker=tracker,
        event_history=history,
    )
    set_dashboard_server(server)
    yield TestClient(create_app()), ledger, history
    set_dashboard_server(None)  # type: ignore[arg-type]


def _record_finished(
    history: EventHistoryService,
    task_id: str,
    status: str = "succeeded",
    *,
    initiator: str = "operator",
    executor: str = "minecraft",
    source: str = "agent",
) -> None:
    history.record(
        EventRecord(
            type=CoreEvents.TASK_CHANGED,
            event_name=CoreEvents.TASK_CHANGED,
            source="TaskLedger",
            data={
                "task_id": task_id,
                "status": status,
                "initiator": initiator,
                "executor": executor,
                "source": source,
                "summary": f"delivery: 完成（{task_id}）",
                "snapshot": {"instruction": f"指令 {task_id}"},
                "timestamp_ms": 1_760_000_000_000,
            },
        )
    )


def test_running_tasks_from_ledger(client) -> None:
    tc, ledger, _history = client
    ledger.register(
        task_id="deleg_1",
        provider="framework",
        tool="framework_delegate",
        initiator="operator",
        executor="minecraft",
        status="running",
        source="agent",
        snapshot={"instruction": "建一座木头房子"},
    )
    ledger.register(
        task_id="deleg_2",
        provider="framework",
        tool="framework_delegate",
        initiator="streamer",
        executor="minecraft",
        status="waiting_for_decision",
        source="agent",
        snapshot={"instruction": "探索东侧"},
    )
    # provider 型自驱执行：executor 是提供者名（maicraft），归属按发起 Agent 推导
    ledger.register(
        task_id="exec_1",
        provider="maicraft",
        tool="maicraft_execute",
        initiator="minecraft",
        executor="maicraft",
        status="running",
        source="provider",
        snapshot={},
    )

    resp = tc.get("/api/v1/tasks")
    assert resp.status_code == 200
    body = resp.json()
    running = {card["task_id"]: card for card in body["running"]}
    assert set(running) == {"deleg_1", "deleg_2", "exec_1"}
    assert running["deleg_1"]["instruction"] == "建一座木头房子"
    assert running["deleg_1"]["status"] == "running"
    assert running["deleg_1"]["initiator"] == "operator" and running["deleg_1"]["executor"] == "minecraft"
    assert running["deleg_2"]["status"] == "waiting_for_decision"
    # 归属推导：agent 型 = 执行 Agent；provider 型 = 发起 Agent
    assert running["deleg_1"]["owner_agent"] == "minecraft"
    assert running["deleg_2"]["owner_agent"] == "minecraft"
    assert running["exec_1"]["owner_agent"] == "minecraft", "自驱执行归属发起 Agent，不再被执行侧标识挡住"


def test_finished_tasks_from_event_history(client) -> None:
    tc, _ledger, history = client
    _record_finished(history, "deleg_done", status="succeeded")
    # 非终态末态（进行中）不进完结区——由账本快照覆盖
    history.record(
        EventRecord(
            type=CoreEvents.TASK_CHANGED,
            event_name=CoreEvents.TASK_CHANGED,
            source="TaskLedger",
            data={"task_id": "deleg_live", "status": "running", "timestamp_ms": 1_760_000_000_001},
        )
    )

    resp = tc.get("/api/v1/tasks")
    assert resp.status_code == 200
    body = resp.json()
    _record_finished(
        history,
        "exec_done",
        initiator="minecraft",
        executor="maicraft",
        source="provider",
    )
    finished = {card["task_id"]: card for card in body["finished"]}
    assert set(finished) == {"deleg_done"}
    assert finished["deleg_done"]["summary"] == "delivery: 完成（deleg_done）"
    assert finished["deleg_done"]["instruction"] == "指令 deleg_done"
    assert finished["deleg_done"]["owner_agent"] == "minecraft"
    assert all(card["task_id"] != "deleg_live" for card in body["running"])


def test_tasks_503_without_tracker(tmp_path: Path) -> None:
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.config.service import ConfigService
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    svc = ConfigService(base_dir=str(tmp_path))
    svc.initialize()
    server = DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60215),
    )
    set_dashboard_server(server)
    try:
        tc = TestClient(create_app())
        resp = tc.get("/api/v1/tasks")
        assert resp.status_code == 503
        assert "任务基建未装配" in resp.json()["detail"]
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]
