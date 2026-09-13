"""Simulator API 测试：状态 / 回放日期 / 人设与礼物 CRUD。

注：使用真实 SimulatorService（tmp SQLiteStore + setup），不触发真实 LLM
（mode=generate 且无 LLMManager 注入时 setup 只落数据平面，CRUD 均可用）。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from src.modules.events.event_bus import EventBus
from src.modules.simulator import SimulatorService
from src.modules.simulator.config_schema import SimulatorConfigSchema
from src.modules.simulator.seed_data import seed_simulator_data
from src.modules.storage import SQLiteStore


class _FakeConfigService:
    """仅暴露 main_config 的最小 ConfigService 替身"""

    def __init__(self) -> None:
        self.main_config = {"simulator": {"enabled": False}}


def _make_service(store: SQLiteStore) -> SimulatorService:
    return SimulatorService(event_bus=EventBus(), sqlite_store=store)


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="sim-api-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def client(temp_db_path: Path) -> Generator[TestClient, None, None]:
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    store = SQLiteStore(temp_db_path)
    await store.initialize()
    await seed_simulator_data(store)
    service = _make_service(store)
    await service.setup(_FakeConfigService(), auto_start=False)  # type: ignore[arg-type]

    server = DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        config_service=None,  # type: ignore[arg-type]
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
    )
    server.simulator_service = service  # type: ignore[attr-defined]
    set_dashboard_server(server)
    yield TestClient(create_app())
    set_dashboard_server(None)
    await store.close()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_reports_mode_and_availability(client: TestClient) -> None:
    body = client.get("/api/v1/simulator/status").json()
    assert body["is_available"] is True
    assert body["is_running"] is False
    assert body["mode"] == "off"
    assert body["replay_progress"] is None


# ---------------------------------------------------------------------------
# replay dates
# ---------------------------------------------------------------------------


def test_replay_dates_empty(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """event_history 表无录制记录时返回空列表。

    端点经默认 store 工厂取连接；打桩到临时空库，测试只关心"空表 → 空列表"的契约
    （默认库在本机可能存有历史录制）。
    """
    import asyncio

    from src.modules.storage.sqlite_store import SQLiteStore

    async def _make() -> SQLiteStore:
        store = SQLiteStore(tmp_path / "replay-dates.db")
        await store.initialize()
        return store

    store = asyncio.run(_make())
    monkeypatch.setattr("src.modules.dashboard.api.simulator.get_default_store", lambda: store)
    try:
        body = client.get("/api/v1/simulator/replay/dates").json()
        assert body == {"dates": []}
    finally:
        asyncio.run(store.close())


def test_replay_dates_from_event_history(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """event_history 表有弹幕录制时按时间正序返回日期（其他事件名不混入）。"""
    import asyncio

    from src.modules.events.names import CoreEvents
    from src.modules.storage.sqlite_store import SQLiteStore

    async def _make() -> SQLiteStore:
        store = SQLiteStore(tmp_path / "replay-dates-populated.db")
        await store.initialize()
        await store.insert_event(
            record_id="rec-2",
            event_name=CoreEvents.ROOM_MESSAGE_DANMAKU,
            timestamp_ms=1_790_000_000_000,  # 晚日期
            payload_json="{}",
        )
        await store.insert_event(
            record_id="rec-1",
            event_name=CoreEvents.ROOM_MESSAGE_DANMAKU,
            timestamp_ms=1_750_000_000_000,  # 早日期
            payload_json="{}",
        )
        await store.insert_event(
            record_id="rec-noise",
            event_name="core.startup",
            timestamp_ms=1_760_000_000_000,
            payload_json="{}",
        )
        return store

    store = asyncio.run(_make())
    monkeypatch.setattr("src.modules.dashboard.api.simulator.get_default_store", lambda: store)
    try:
        body = client.get("/api/v1/simulator/replay/dates").json()
        assert body["dates"] == sorted(body["dates"])
        assert len(body["dates"]) == 2  # core.startup 不计入
    finally:
        asyncio.run(store.close())


# ---------------------------------------------------------------------------
# personas CRUD
# ---------------------------------------------------------------------------


def test_persona_create_list_update_delete(client: TestClient) -> None:
    # 列表：内置种子
    body = client.get("/api/v1/simulator/personas").json()
    assert body["is_available"] is True
    seeded_ids = {p["user_id"] for p in body["personas"]}
    assert "sim_veteran_01" in seeded_ids

    # 新增
    resp = client.post(
        "/api/v1/simulator/personas",
        json={
            "user_nickname": "API新建观众",
            "role": "fan",
            "personality": "热情",
            "speaking_style": "活泼",
        },
    )
    assert resp.status_code == 200
    created = resp.json()
    assert created["success"] is True
    user_id = created["persona"]["user_id"]

    # 更新
    resp = client.patch(
        f"/api/v1/simulator/personas/{user_id}",
        json={"user_nickname": "API改名观众", "context_window_size": 7},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    listed = client.get("/api/v1/simulator/personas").json()["personas"]
    target = next(p for p in listed if p["user_id"] == user_id)
    assert target["user_nickname"] == "API改名观众"
    assert target["context_window_size"] == 7

    # 删除
    resp = client.delete(f"/api/v1/simulator/personas/{user_id}")
    assert resp.json()["success"] is True
    listed = client.get("/api/v1/simulator/personas").json()["personas"]
    assert all(p["user_id"] != user_id for p in listed)


def test_persona_create_duplicate_nickname_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/simulator/personas",
        json={
            "user_nickname": "三楼老王",
            "role": "fan",
            "personality": "重复昵称",
            "speaking_style": "随意",
        },
    )
    body = resp.json()
    assert body["success"] is False
    assert "已存在" in body["message"]


def test_persona_update_nonexistent(client: TestClient) -> None:
    resp = client.patch("/api/v1/simulator/personas/ghost", json={"personality": "x"})
    assert resp.json()["success"] is False


# ---------------------------------------------------------------------------
# gifts CRUD
# ---------------------------------------------------------------------------


def test_gift_create_list_update_delete(client: TestClient) -> None:
    body = client.get("/api/v1/simulator/gifts").json()
    assert body["is_available"] is True
    assert any(g["gift_id"] == "small_heart" for g in body["gifts"])

    resp = client.post(
        "/api/v1/simulator/gifts",
        json={"gift_id": "api_gift", "gift_name": "API礼物", "category": "normal", "weight": 3},
    )
    assert resp.json()["success"] is True

    resp = client.patch("/api/v1/simulator/gifts/api_gift", json={"weight": 9})
    assert resp.json()["success"] is True
    listed = client.get("/api/v1/simulator/gifts").json()["gifts"]
    assert next(g for g in listed if g["gift_id"] == "api_gift")["weight"] == 9

    resp = client.delete("/api/v1/simulator/gifts/api_gift")
    assert resp.json()["success"] is True
    assert all(g["gift_id"] != "api_gift" for g in client.get("/api/v1/simulator/gifts").json()["gifts"])


def test_gift_create_duplicate_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/simulator/gifts",
        json={"gift_id": "small_heart", "gift_name": "重复", "category": "normal"},
    )
    body = resp.json()
    assert body["success"] is False
    assert "已存在" in body["message"]
