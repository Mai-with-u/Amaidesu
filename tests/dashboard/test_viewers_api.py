"""Viewers API 测试：观众域只读端点（列表 / 分析 / 档案 / 对话 / 贡献 / 场次）。

使用真实 SQLiteDatabase（临时库）经 DashboardServer 挂载，走完整 HTTP 层；
造数直连仓储（``store.viewers.upsert_viewer_*`` / ``store.chat.insert_*``），
不经过业务写链。
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
from src.modules.storage.database import SQLiteDatabase
from src.modules.time_utils import now_ms


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="viewers-api-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


_server_ref_cache = {}


def _run(coro) -> None:
    """同步用例内驱动异步仓储调用（与既有本文件模式一致）。"""
    loop = asyncio.new_event_loop()
    loop.run_until_complete(coro)
    loop.close()


@pytest.fixture
def client(temp_db_path: Path) -> Generator[TestClient, None, None]:
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    async def _build():
        from src.modules.events.event_history import EventHistoryService

        store = SQLiteDatabase(temp_db_path)
        await store.initialize()
        bus = EventBus()
        manager = LiveSessionManager(store.sessions, store.chat, bus)
        await manager.start()
        event_history = EventHistoryService(max_events=100)

        server = DashboardServer(
            event_bus=bus,
            config_service=None,  # type: ignore[arg-type]
            dashboard_config=DashboardConfig(host="127.0.0.1", port=60215),
            session_manager=manager,
            viewer_repo=store.viewers,
            chat_repo=store.chat,
            event_history=event_history,
        )
        return store, bus, manager, server

    loop = asyncio.new_event_loop()
    store, bus, manager, server = loop.run_until_complete(_build())
    loop.close()

    _server_ref_cache["server"] = server
    _server_ref_cache["store"] = store
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
    store = _server_ref_cache["store"]

    async def _seed():
        for i in range(count):
            await store.viewers.upsert_viewer_message(
                platform="bilibili",
                user_id=f"u{i}",
                user_name=f"观众{i}",
                timestamp_ms=1_700_000_000_000 + i,
            )

    _run(_seed())


# ===== 列表：搜索 / 排序 / 分页 =====


def test_viewers_empty(client: TestClient) -> None:
    body = client.get("/api/v1/viewers").json()
    assert body["total"] == 0
    assert body["items"] == []


def test_viewers_seeded_fields(client: TestClient) -> None:
    _seed_viewers(3)
    body = client.get("/api/v1/viewers").json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    first = body["items"][0]
    assert set(first) == {
        "platform",
        "user_id",
        "user_name",
        "message_count",
        "gift_count",
        "replied_count",
        "interaction_count",
        "paid_count",
        "paid_amount",
        "last_active_ms",
    }
    assert {item["user_id"] for item in body["items"]} == {"u0", "u1", "u2"}


def test_viewers_order_by_gift(client: TestClient) -> None:
    _seed_viewers(3)
    store = _server_ref_cache["store"]
    _run(store.viewers.upsert_viewer_gift(platform="bilibili", user_id="u0", user_name="观众0", timestamp_ms=1_700_000_001_000))
    body = client.get("/api/v1/viewers", params={"order_by": "gift_count", "limit": 2}).json()
    assert body["items"][0]["user_id"] == "u0"
    assert body["items"][0]["gift_count"] == 1


def test_viewers_search_and_pagination(client: TestClient) -> None:
    _seed_viewers(5)
    body = client.get("/api/v1/viewers", params={"search": "观众3"}).json()
    assert body["total"] == 1
    assert body["items"][0]["user_id"] == "u3"
    # 分页：total 不随 limit 变
    body = client.get("/api/v1/viewers", params={"limit": 2, "offset": 4}).json()
    assert body["total"] == 5
    assert len(body["items"]) == 1


def test_viewers_invalid_order_by_returns_400(client: TestClient) -> None:
    r = client.get("/api/v1/viewers", params={"order_by": "user_id; DROP TABLE viewers"})
    assert r.status_code == 400


# ===== 档案 / 对话 / 贡献 / 场次 =====


def _seed_full_viewer() -> None:
    """造一个完整观众：2 场次发言 + 主播回复 + 礼物 + SC。"""
    store = _server_ref_cache["store"]
    base = 1_700_000_000_000

    async def _seed():
        await store.viewers.upsert_viewer_message(platform="bilibili", user_id="u_full", user_name="富观众", timestamp_ms=base + 5_000)
        await store.viewers.upsert_viewer_replied(platform="bilibili", user_id="u_full", timestamp_ms=base + 5_500)
        await store.chat.insert_live_chat(
            live_session_id=1,
            timestamp_ms=base,
            sender_role="viewer",
            sender_id="u_full",
            sender_name="富观众",
            content="第一条",
            message_type="danmaku",
            message_id="fm1",
        )
        await store.chat.insert_live_chat(
            live_session_id=1,
            timestamp_ms=base + 1_000,
            sender_role="viewer",
            sender_id="u_full",
            sender_name="富观众",
            content="第二条",
            message_type="danmaku",
            message_id="fm2",
        )
        await store.chat.insert_live_chat(
            live_session_id=2,
            timestamp_ms=base + 9_000,
            sender_role="assistant",
            content="谢谢支持",
            message_type="speech",
            reply_to_message_id="fm2",
        )
        await store.chat.insert_gift(
            live_session_id=1,
            timestamp_ms=base + 2_000,
            user_id="u_full",
            user_name="富观众",
            gift_name="小心心",
            quantity=2,
            currency="bilibili_gold_coin",
        )
        await store.chat.insert_super_chat(
            live_session_id=2,
            timestamp_ms=base + 8_000,
            user_id="u_full",
            user_name="富观众",
            total_price=30_000,
            currency="bilibili_gold_coin",
            message="加油",
        )

    _run(_seed())


def test_viewer_detail_aggregates(client: TestClient) -> None:
    _seed_full_viewer()
    body = client.get("/api/v1/viewers/u_full").json()
    assert body["user_id"] == "u_full"
    assert body["user_name"] == "富观众"
    assert body["message_count"] == 1
    assert body["replied_count"] == 1
    assert body["first_seen_ms"] == 1_700_000_000_000  # 三表最早（发言）
    assert body["gift_total_count"] == 2
    assert body["sc_total_amount"] == 30.0
    assert body["sc_total_count"] == 1
    # 只按观众发言行聚合：发言都在场次 1（场次 2 仅主播回复行）
    assert body["session_count"] == 1


def test_viewer_detail_not_found(client: TestClient) -> None:
    assert client.get("/api/v1/viewers/ghost").status_code == 404


def test_viewer_dialogue_interleaves_and_pages(client: TestClient) -> None:
    _seed_full_viewer()
    body = client.get("/api/v1/viewers/u_full/messages", params={"limit": 1}).json()
    # 首批 limit=1：仅最新观众消息 fm2（base+1000），无更早交织
    assert [item["message_id"] for item in body["items"] if item["kind"] == "viewer"] == ["fm2"]
    assert body["next_before"] == 1_700_000_001_000

    # 无 limit 约束（默认 30）：两批观众消息 + 主播回复交织
    body = client.get("/api/v1/viewers/u_full/messages").json()
    kinds = [(item["kind"], item["timestamp_ms"]) for item in body["items"]]
    assert kinds == [
        ("viewer", 1_700_000_000_000),
        ("viewer", 1_700_000_001_000),
        ("reply", 1_700_000_009_000),
    ]
    assert body["next_before"] is None  # 观众消息不足 limit，翻尽

    # 游标翻更早：只取 fm1
    body = client.get("/api/v1/viewers/u_full/messages", params={"before_timestamp_ms": 1_700_000_001_000}).json()
    assert [item["message_id"] for item in body["items"]] == ["fm1"]


def test_viewer_contributions_desc_and_summary(client: TestClient) -> None:
    _seed_full_viewer()
    body = client.get("/api/v1/viewers/u_full/contributions").json()
    assert body["gift_total_count"] == 2
    assert body["sc_total_amount"] == 30.0
    assert body["sc_total_count"] == 1
    assert len(body["gifts"]) == 1
    assert body["gifts"][0]["gift_name"] == "小心心"
    assert body["super_chats"][0]["message"] == "加油"


def test_viewer_sessions(client: TestClient) -> None:
    _seed_full_viewer()
    body = client.get("/api/v1/viewers/u_full/sessions").json()
    # 只聚合 viewer 行：u_full 的发言都在场次 1（场次 2 仅 assistant 回复行）
    assert len(body["items"]) == 1
    assert body["items"][0]["live_session_id"] == 1
    assert body["items"][0]["message_count"] == 2


# ===== 互动分析 =====


def test_insights_buckets_and_daily(client: TestClient) -> None:
    store = _server_ref_cache["store"]
    now = now_ms()

    async def _seed():
        await store.viewers.upsert_viewer_message(platform="bilibili", user_id="a_today", user_name="今日观众", timestamp_ms=now)
        await store.viewers.upsert_viewer_message(
            platform="bilibili", user_id="b_week", user_name="本周观众", timestamp_ms=now - 3 * 86_400_000
        )
        await store.viewers.upsert_viewer_message(
            platform="bilibili",
            user_id="c_old", user_name="老观众", timestamp_ms=now - 60 * 86_400_000
        )
        await store.chat.insert_live_chat(
            live_session_id=1,
            timestamp_ms=now,
            sender_role="viewer",
            sender_id="a_today",
            sender_name="今日观众",
            content="今天第一条",
            message_type="danmaku",
        )
        await store.chat.insert_live_chat(
            live_session_id=1,
            timestamp_ms=now - 1_000,
            sender_role="viewer",
            sender_id="a_today",
            sender_name="今日观众",
            content="今天第二条",
            message_type="danmaku",
        )

    _run(_seed())
    body = client.get("/api/v1/viewers/insights").json()
    assert body["total_viewers"] == 3
    assert body["active_today"] == 1
    assert body["active_week"] == 1
    assert body["active_month"] == 0
    assert body["active_older"] == 1
    assert body["never_replied"] == 3
    assert body["gift_viewers"] == 0
    # 今天的两条弹幕聚合为一个日期点
    assert len(body["daily_danmaku"]) == 1
    assert body["daily_danmaku"][0]["count"] == 2


def test_insights_requires_repos(client: TestClient) -> None:
    """观众仓储未装配时列表端点 503（server 极简启动形态）。"""
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    async def _build():
        bus = EventBus()
        return DashboardServer(
            event_bus=bus,
            config_service=None,  # type: ignore[arg-type]
            dashboard_config=DashboardConfig(host="127.0.0.1", port=60216),
        )

    loop = asyncio.new_event_loop()
    bare_server = loop.run_until_complete(_build())
    loop.close()
    set_dashboard_server(bare_server)
    app = create_app()
    with TestClient(app) as bare_client:
        assert bare_client.get("/api/v1/viewers").status_code == 503
        assert bare_client.get("/api/v1/viewers/insights").status_code == 503
    set_dashboard_server(None)
