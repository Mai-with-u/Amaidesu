"""Sessions API 测试：场次列表 / 开启 / 结束 / 删除。

使用真实 LiveSessionManager（tmp SQLiteStore + EventBus），走完整 HTTP 层。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from src.modules.events.event_bus import EventBus
from src.modules.session import LiveSessionManager
from src.modules.storage import SQLiteStore


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="sessions-api-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


_server_ref_cache = {}


def _server_ref():
    """返回最近一次 fixture 构造的 DashboardServer（供造数用例直连底层组件）。"""
    return _server_ref_cache["server"]


@pytest.fixture
def client(temp_db_path: Path) -> Generator[TestClient, None, None]:
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    async def _build():
        from src.modules.events.event_history import EventHistoryService

        store = SQLiteStore(temp_db_path)
        await store.initialize()
        bus = EventBus()
        manager = LiveSessionManager(store, bus)
        await manager.start()
        event_history = EventHistoryService(max_events=100, persist=False)

        server = DashboardServer(
            event_bus=bus,
            config_service=None,  # type: ignore[arg-type]
            dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
            session_manager=manager,
            event_history=event_history,
        )
        return store, bus, manager, server

    import asyncio

    loop = asyncio.new_event_loop()
    store, bus, manager, server = loop.run_until_complete(_build())
    loop.close()

    _server_ref_cache["server"] = server
    set_dashboard_server(server)
    app = create_app()
    with TestClient(app) as c:
        yield c

    set_dashboard_server(None)

    async def _teardown():
        if manager.active_pk is not None:
            await manager.close_session()
        await bus.cleanup()
        await store.close()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_teardown())
    loop.close()


def test_open_list_close_flow(client: TestClient) -> None:
    # 初始：无任何场次（启动不自动开新场次）
    body = client.get("/api/v1/live-sessions").json()
    assert body["active_session_id"] is None
    assert body["items"] == []

    # 开启
    opened = client.post("/api/v1/live-sessions/open", json={"title": "晚间场"}).json()
    pk = opened["live_session_id"]
    assert pk > 0

    # 列表显示进行中场次
    body = client.get("/api/v1/live-sessions").json()
    assert body["active_session_id"] == pk
    active = [item for item in body["items"] if item["is_active"]]
    assert len(active) == 1
    assert active[0]["title"] == "晚间场"
    assert active[0]["ended_at_ms"] is None

    # 空场次结束 → 整行丢弃（防膨胀）
    closed = client.post(f"/api/v1/live-sessions/{pk}/close").json()
    assert closed["success"] is True
    body = client.get("/api/v1/live-sessions").json()
    assert body["active_session_id"] is None
    assert all(item["live_session_id"] != pk for item in body["items"])


def test_close_mismatched_session_returns_409(client: TestClient) -> None:
    client.post("/api/v1/live-sessions/open", json={})
    client.post("/api/v1/live-sessions/open", json={})
    # 开第二个场次时第一个已被自动结束；指定一个已结束的 id → 409
    stale = client.get("/api/v1/live-sessions").json()["active_session_id"]
    r = client.post(f"/api/v1/live-sessions/{stale + 100000}/close")
    assert r.status_code == 409


def test_delete_session(client: TestClient) -> None:
    opened = client.post("/api/v1/live-sessions/open", json={"title": "待删"}).json()
    pk = opened["live_session_id"]
    r = client.delete(f"/api/v1/live-sessions/{pk}")
    assert r.status_code == 200
    assert r.json()["success"] is True
    # 再删 → 404
    r = client.delete(f"/api/v1/live-sessions/{pk}")
    assert r.status_code == 404


def test_session_timeline_merges_details_and_events(client: TestClient) -> None:
    """回看时间线：明细行（弹幕/发言/礼物）与事件历史（决策/阶段）按时间合并。"""
    import asyncio

    from src.modules.events.event_history import EventRecord

    # 开启一场显式场次用于回看（启动不自动开新场次，需手动开）
    opened = client.post("/api/v1/live-sessions/open", json={"title": "回看测试"}).json()
    pk = opened["live_session_id"]

    # 直连底层组件造数据（HTTP 层没有造数端点）
    server = _server_ref()
    manager = server.session_manager
    loop = asyncio.new_event_loop()

    async def _seed():
        await manager.store.insert_live_chat(
            live_session_id=pk,
            timestamp_ms=1_700_000_000_000,
            sender_role="viewer",
            sender_id="u1",
            sender_name="观众A",
            content="主播玩什么？",
            message_type="danmaku",
            message_id="msg_seed_1",
        )
        await manager.store.insert_live_chat(
            live_session_id=pk,
            timestamp_ms=1_700_000_005_000,
            sender_role="assistant",
            sender_name="主播",
            content="今天玩《双人成行》！",
            message_type="speak",
            reply_to_message_id="msg_seed_1",
        )
        if server.event_history is not None:
            server.event_history.record(
                EventRecord(
                    id="evt-decision-1",
                    type="planner.decision",
                    level="info",
                    source=str(pk),
                    summary="决策",
                    data={
                        "live_session_id": pk,
                        "round_id": "rnd_1",
                        "should_reply": True,
                        "speech": "今天玩《双人成行》！",
                        "reply_to_message_id": "msg_seed_1",
                    },
                    timestamp=1_700_000_004.0,
                )
            )

    loop.run_until_complete(_seed())
    loop.close()

    body = client.get(f"/api/v1/live-sessions/{pk}/timeline").json()
    items = body["items"]
    kinds = [item["kind"] for item in items]
    assert "danmaku" in kinds
    assert "speech" in kinds
    decision = next(item for item in items if item["kind"] == "event" and item["event_type"] == "planner.decision")
    assert decision["data"]["round_id"] == "rnd_1"
    # 回复关联在明细行上可直接查询
    speech = next(item for item in items if item["kind"] == "speech")
    assert speech["reply_to_message_id"] == "msg_seed_1"


def test_list_sessions_with_filters(client: TestClient) -> None:
    """来源与标题筛选走服务端；非法来源返回 400。

    前两个显式场次都注入一条明细再被自动结束（空场次会被整行丢弃，
    防膨胀语义详见 test_open_list_close_flow）。
    """
    import asyncio

    first = client.post("/api/v1/live-sessions/open", json={"title": "筛选测试场"}).json()["live_session_id"]
    manager = _server_ref().session_manager
    loop = asyncio.new_event_loop()

    async def _seed():
        await manager.store.insert_live_chat(
            live_session_id=first,
            timestamp_ms=1_700_000_000_000,
            sender_role="viewer",
            content="让这场不被丢弃",
            message_type="danmaku",
        )

    loop.run_until_complete(_seed())
    loop.close()

    second = client.post("/api/v1/live-sessions/open", json={"title": "另一个场次"}).json()["live_session_id"]
    client.post(f"/api/v1/live-sessions/{second}/close")

    manual = client.get("/api/v1/live-sessions", params={"source": "manual"}).json()
    assert [item["title"] for item in manual["items"]] == ["筛选测试场"]
    assert all(item["source"] == "manual" for item in manual["items"])

    keyword = client.get("/api/v1/live-sessions", params={"q": "筛选测试"}).json()
    assert len(keyword["items"]) == 1
    assert keyword["items"][0]["title"] == "筛选测试场"

    bad = client.get("/api/v1/live-sessions", params={"source": "hacker"})
    assert bad.status_code == 400
