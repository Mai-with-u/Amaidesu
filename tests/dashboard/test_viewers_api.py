"""Viewers API 测试：观众统计只读端点。

使用真实 SQLiteStore（临时库）经 DashboardServer 挂载，走完整 HTTP 层；
造数直连 ``store.upsert_viewer_*``，不经过业务写链。
"""

from __future__ import annotations

import asyncio
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
    td = Path(tempfile.mkdtemp(prefix="viewers-api-"))
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
            dashboard_config=DashboardConfig(host="127.0.0.1", port=60215),
            session_manager=manager,
            event_history=event_history,
        )
        return store, bus, manager, server

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


def _seed_viewers(count: int) -> None:
    """直连底层 store 造 viewer 行（发言计数区分排名）。"""
    server = _server_ref()
    store = server.session_manager.store

    async def _seed():
        for i in range(count):
            await store.upsert_viewer_message(
                user_id=f"u{i}",
                user_name=f"观众{i}",
                timestamp_ms=1_700_000_000_000 + i,
            )

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_seed())
    loop.close()


def test_viewers_empty(client: TestClient) -> None:
    body = client.get("/api/v1/viewers").json()
    assert body["count"] == 0
    assert body["top"] == []


def test_viewers_seeded_count_and_fields(client: TestClient) -> None:
    _seed_viewers(3)
    body = client.get("/api/v1/viewers").json()
    assert body["count"] == 3
    assert len(body["top"]) == 3
    first = body["top"][0]
    assert set(first) == {
        "user_id",
        "user_name",
        "message_count",
        "gift_count",
        "replied_count",
        "interaction_count",
        "last_active_ms",
    }
    # 排序：默认按 message_count DESC；计数均为 1，按插入顺序即可
    assert {item["user_id"] for item in body["top"]} == {"u0", "u1", "u2"}
    assert all(item["message_count"] == 1 for item in body["top"])


def test_viewers_order_by_gift_and_limit(client: TestClient) -> None:
    _seed_viewers(3)
    server = _server_ref()
    store = server.session_manager.store

    async def _seed_gift():
        await store.upsert_viewer_gift(
            user_id="u0",
            user_name="观众0",
            timestamp_ms=1_700_000_001_000,
        )

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_seed_gift())
    loop.close()

    body = client.get("/api/v1/viewers", params={"order_by": "gift_count", "limit": 2}).json()
    assert body["count"] == 2
    assert body["top"][0]["user_id"] == "u0"
    assert body["top"][0]["gift_count"] == 1


def test_viewers_invalid_order_by_returns_400(client: TestClient) -> None:
    r = client.get("/api/v1/viewers", params={"order_by": "user_id; DROP TABLE viewers"})
    assert r.status_code == 400
