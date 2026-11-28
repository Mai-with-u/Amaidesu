"""MaiGO 同步桥测试：配置校验、HTTP 客户端、开播签出、心跳、签入与离线补推。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import main as app_main
from src.modules.config.infra_schemas import InfraRootConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.events.payloads.rundown import RundownChangedPayload
from src.modules.maigo_sync import (
    HttpMaiGOClient,
    MaiGOBridge,
    MaiGORejected,
    MaiGOSyncConfigSchema,
)
from src.modules.maigo_sync import backfill as maigo_backfill_mod
from src.modules.maigo_sync import bridge as maigo_bridge_mod
from src.modules.maigo_sync.backfill import StartupHistoryBackfiller
from src.modules.maigo_sync.pending_log import CachedPromiseEntry, format_brief_extension
from src.modules.storage.database import SQLiteDatabase
from src.modules.types.session_kind import SESSION_KIND_LIVE, SESSION_KIND_REHEARSAL


class _StubMaiGOClient:
    """实现 MaiGOClient 协议的内存替身，支持预设响应与故障注入。"""

    def __init__(self, checkout_response: Optional[Dict[str, Any]] = None) -> None:
        self.checkout_response = checkout_response or {
            "checkout_id": "chk-101",
            "brief_etag": "etag-v1",
            "stable_brief": "# 麦麦的今日生活（截至目前）\n- 下午：在B站刷到用粘土捏鱼的视频并留言夸手艺逼真。",
            "live_context": "",
            "open_promises": [
                {
                    "id": 9,
                    "anchor_type": "promise",
                    "content": "今晚直播在 MC 里建一座新灯塔",
                    "status": "open",
                    "source_branch": "maibot",
                    "save_id": "save-ocean-1",
                }
            ],
            "topic_cards": [
                {
                    "id": 21,
                    "title": "粘土手工甲鱼视频",
                    "summary": "下午刷到粘土手工视频并留过评",
                    "reaction": "想顺便聊聊自己以前手工翻车",
                    "source_branch": "bilibili_ops",
                    "consumed_branches": ["maibot"],
                    "transition_hint": "（群里提过一嘴）",
                }
            ],
            "person_directory": [
                {
                    "person_id": 7,
                    "platform": "bilibili",
                    "external_id": "20002",
                    "display_name": "老熟人甲",
                    "prompt_hint": "老熟人甲：在群里聊过下午的手工视频，刚在游戏里见过。",
                },
                {
                    "person_id": 7,
                    "platform": "minecraft",
                    "external_id": "00000000-0000-0000-0000-000000000001",
                    "display_name": "老熟人甲",
                    "prompt_hint": "老熟人甲：在群里聊过下午的手工视频，也是B站同一位好友。",
                },
            ],
            "save_brief": "save-ocean-1：今天群里聊过沿海建灯塔计划。",
        }
        self.checkouts: List[Dict[str, Any]] = []
        self.heartbeats: List[Tuple[str, Dict[str, Any]]] = []
        self.checkins: List[Tuple[str, Dict[str, Any]]] = []
        self.posted_batches: List[List[Dict[str, Any]]] = []
        self.archive_batches: List[Dict[str, Any]] = []
        self.knowledge_batches: List[Dict[str, Any]] = []
        self.recall_calls: List[Dict[str, Any]] = []
        self.recall_response: Dict[str, Any] = {"ok": True, "text": "", "strong_hit": False, "hits": []}
        self.fail_checkout: Optional[Exception] = None
        self.fail_heartbeat: Optional[Exception] = None
        self.fail_checkin_times: int = 0
        self.fail_post_after: Optional[int] = None
        self.fail_post_error: Optional[Exception] = None
        self.fail_archive_error: Optional[Exception] = None
        self.fail_world_archive_error: Optional[Exception] = None
        self.fail_knowledge_error: Optional[Exception] = None
        self.checkin_error: Exception = RuntimeError("MaiGO 离线")
        self.closed = False

    async def checkout(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.checkouts.append(dict(payload))
        if self.fail_checkout is not None:
            raise self.fail_checkout
        return dict(self.checkout_response)

    async def heartbeat(self, checkout_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.heartbeats.append((checkout_id, dict(payload)))
        if self.fail_heartbeat is not None:
            raise self.fail_heartbeat
        return {"ok": True, "checkout_id": checkout_id, "consumed_count": len(payload.get("consumed_seed_ids") or [])}

    async def checkin(self, checkout_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.checkins.append((checkout_id, dict(payload)))
        if self.fail_checkin_times > 0:
            self.fail_checkin_times -= 1
            raise self.checkin_error
        return {"ok": True, "checkout_id": checkout_id}

    async def consume_topic(self, topic_id: int, *, branch_id: str) -> Dict[str, Any]:
        return {"ok": True, "topic_id": topic_id, "branch_id": branch_id}

    async def post_events(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        if self.fail_post_after is not None and len(self.posted_batches) >= self.fail_post_after:
            raise self.fail_post_error or ConnectionError("MaiGO 暂不可达")
        self.posted_batches.append([dict(item) for item in events])
        return {"ok": True, "accepted": len(events), "deduplicated": 0}

    async def ingest_archive(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("episode") is not None and self.fail_archive_error is not None:
            raise self.fail_archive_error
        if payload.get("episode") is None and "external_episode_id" not in payload and self.fail_world_archive_error:
            raise self.fail_world_archive_error
        self.archive_batches.append(dict(payload))
        return {"ok": True, "accepted": len(payload.get("records") or [])}

    async def ingest_knowledge(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.fail_knowledge_error is not None:
            raise self.fail_knowledge_error
        self.knowledge_batches.append(dict(payload))
        return {"ok": True, "relations_accepted": len(payload.get("relations") or [])}

    async def recall(
        self,
        *,
        query: str,
        days: int = 30,
        limit: int = 3,
        target_visibility: str = "public",
        branch_id: str = "",
        high_confidence_only: bool = True,
        include_structured: bool = True,
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        self.recall_calls.append(
            {
                "query": query,
                "days": days,
                "limit": limit,
                "target_visibility": target_visibility,
                "branch_id": branch_id,
                "high_confidence_only": high_confidence_only,
                "include_structured": include_structured,
                "timeout_ms": timeout_ms,
            }
        )
        return dict(self.recall_response)

    async def close(self) -> None:
        self.closed = True


def test_config_schema_and_infra_section_order() -> None:
    cfg = MaiGOSyncConfigSchema()
    assert cfg.enabled is False and cfg.url == "" and cfg.branch_id == "amaidesu"

    norm = MaiGOSyncConfigSchema(enabled=True, url="http://127.0.0.1:8768/", branch_id="Amaidesu")
    assert norm.url == "http://127.0.0.1:8768" and norm.branch_id == "amaidesu"

    with pytest.raises(ValueError, match="http://"):
        MaiGOSyncConfigSchema(enabled=True, url="ftp://127.0.0.1:8768")

    # 守住 infra.toml 段尾必须仍是 maibot_sync（历史配置迁移锚点依赖）
    field_names = list(InfraRootConfig.model_fields.keys())
    assert field_names[-1] == "maibot_sync"
    assert "maigo_sync" in field_names


async def test_http_client_requests_and_4xx_rejection() -> None:
    received: List[Tuple[str, str, Dict[str, Any]]] = []

    async def handle(request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")
        if auth != "Bearer secret-tok":
            return web.json_response({"detail": "unauthorized"}, status=401)
        body = await request.json()
        received.append((request.method, request.path, body))
        return web.json_response({"ok": True, "checkout_id": "chk-http-1"})

    app = web.Application()
    app.router.add_post("/api/v1/sessions/checkout", handle)
    app.router.add_post("/api/v1/sessions/{cid}/heartbeat", handle)
    app.router.add_post("/api/v1/sessions/{cid}/checkin", handle)
    app.router.add_post("/api/v1/topics/{tid}/consume", handle)

    async with TestServer(app) as server:
        client = HttpMaiGOClient(base_url=str(server.make_url("")), token="secret-tok", timeout_ms=2_000)
        try:
            res = await client.checkout({"branch_id": "amaidesu", "external_session_id": "1"})
            assert res["checkout_id"] == "chk-http-1"
            await client.heartbeat("chk-http-1", {"consumed_seed_ids": [21]})
            await client.checkin("chk-http-1", {"ended_at_ms": 2_000, "discarded": False})
            await client.consume_topic(21, branch_id="amaidesu")
            assert len(received) == 4
        finally:
            await client.close()

        bad_client = HttpMaiGOClient(base_url=str(server.make_url("")), token="wrong-tok", timeout_ms=2_000)
        try:
            with pytest.raises(MaiGORejected):
                await bad_client.checkout({"branch_id": "amaidesu", "external_session_id": "2"})
        finally:
            await bad_client.close()


async def test_checkout_cache_rehearsal_skip_and_heartbeat_consumption() -> None:
    bus = EventBus()
    client = _StubMaiGOClient()
    bridge = MaiGOBridge(event_bus=bus, client=client, heartbeat_interval_ms=60_000, retry_delays_ms=())
    bridge.setup()
    try:
        # 彩排场完全跳过，不发起任何远端请求
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=10, kind=SESSION_KIND_REHEARSAL, title="彩排"),
            source="test",
        )
        await bus.wait_until_drained()
        assert len(client.checkouts) == 0
        assert bridge.render_brief_extension() == ""

        # 正式场开播签出并冻结扩展简报，多次读取逐字节一致
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(
                live_session_id=11,
                kind=SESSION_KIND_LIVE,
                title="晚上杂谈",
                platform="bilibili",
                room_id="9001",
                started_at_ms=1_000_000,
            ),
            source="test",
        )
        await bus.wait_until_drained()
        assert len(client.checkouts) == 1
        assert bridge.checkout_id == "chk-101"
        ext1 = bridge.render_brief_extension()
        ext2 = bridge.render_brief_extension()
        assert ext1 == ext2
        assert "粘土捏鱼" in ext1 and "新灯塔" in ext1 and "save-ocean-1" in ext1

        cards = bridge.virtual_cards()
        assert len(cards) == 1 and cards[0].id == -21 and "[今日见闻]" in cards[0].title
        assert bridge.lookup_person_cue("bilibili", "20002") == (
            7,
            "老熟人甲：在群里聊过下午的手工视频，刚在游戏里见过。",
        )
        # Minecraft UUID 不区分大小写命中
        assert bridge.lookup_person_cue("minecraft", "00000000-0000-0000-0000-000000000001") is not None

        # 选用负数虚拟卡后从可用列表移除，并随下一次心跳上报 consumed_seed_ids
        bridge.mark_card_used(-21)
        assert bridge.virtual_cards() == []
        await bus.emit(
            CoreEvents.RUNDOWN_CHANGED,
            RundownChangedPayload(rundown_id="r1", segment_id="talk", segment_title="自由杂谈", index=0, total=2),
            source="test",
        )
        await bus.wait_until_drained()
        assert len(client.heartbeats) == 1
        hb_id, hb_body = client.heartbeats[0]
        assert hb_id == "chk-101"
        assert hb_body["segment_title"] == "自由杂谈"
        assert hb_body["consumed_seed_ids"] == [21]

        # 再次发送心跳不再重复上传已送出的 seed_id
        assert await bridge.flush_heartbeat(save_id="save-ocean-1") is True
        assert client.heartbeats[1][1]["consumed_seed_ids"] == []
        assert client.heartbeats[1][1]["save_id"] == "save-ocean-1"
    finally:
        await bridge.stop()
    assert client.closed is True


async def test_push_settled_session_filters_forgetting_players_and_replays_pending(tmp_path: Path) -> None:
    db = SQLiteDatabase(tmp_path / "amaidesu.db")
    await db.initialize()
    pending_path = tmp_path / "maigo_checkin_pending.jsonl"
    try:
        sid = await db.sessions.insert_live_session(
            stream_id="9001", platform="bilibili", started_at_ms=1_000_000, title="海洋建筑"
        )
        chat_id = await db.chat.insert_live_chat(
            live_session_id=sid,
            timestamp_ms=1_005_000,
            sender_role="assistant",
            content="今晚在海岸边把新灯塔点亮了，而且以前那个灯塔约定算完成啦！",
            message_type="speech",
        )
        await db.archive.insert_line(
            live_session_id=sid,
            text="在MC海岸边建成了新灯塔",
            quote="今晚在海岸边把新灯塔点亮了",
            evidence_chat_id=chat_id,
            game="minecraft",
            save_id="save-ocean-1",
            save_name="海洋一号档",
            highlight=True,
        )
        await db.sessions.update_live_session_overview(
            live_session_id=sid,
            overview="今晚建成了海岸新灯塔",
            updated_at_ms=1_015_000,
        )
        await db.commitments.insert_commitment(
            what="下次再去扩建码头",
            quote="下次再去扩建码头",
            evidence_chat_id=chat_id,
            created_session_id=sid,
            save_id="save-ocean-1",
            now_ms_value=1_006_000,
        )
        # 正常玩家与请求遗忘的玩家
        p_ok = await db.game_players.meet_player(
            game="minecraft",
            uuid="00000000-0000-0000-0000-000000000001",
            name="老熟人甲",
            live_session_id=sid,
            at_ms=1_002_000,
        )
        p_forget = await db.game_players.meet_player(
            game="minecraft",
            uuid="00000000-0000-0000-0000-000000000099",
            name="遗忘玩家",
            live_session_id=sid,
            at_ms=1_003_000,
        )
        await db.game_players.add_interaction(
            player_id=p_ok.id, live_session_id=sid, kind="gift", fact={"item": "面包", "count": 2}, at_ms=1_002_100
        )
        await db.game_players.add_interaction(
            player_id=p_forget.id,
            live_session_id=sid,
            kind="gift",
            fact={"item": "苹果", "count": 1},
            at_ms=1_003_100,
        )
        await db.execute(
            "UPDATE game_players SET forget_requested_at_ms=? WHERE id=?",
            (1_004_000, p_forget.id),
        )
        await db.sessions.close_live_session(live_session_id=sid, ended_at_ms=1_020_000)

        bus = EventBus()
        client = _StubMaiGOClient()
        # 前 2 次 checkin 故意抛异常触发持久化待补推队列
        client.fail_checkin_times = 2
        bridge = MaiGOBridge(
            event_bus=bus,
            client=client,
            pending_log_path=pending_path,
            retry_delays_ms=(0,),
            clock_ms=lambda: 1_025_000,
        )
        bridge.setup()
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=sid, kind=SESSION_KIND_LIVE, started_at_ms=1_000_000),
            source="test",
        )
        await bus.wait_until_drained()
        bridge.mark_promise_resolved(9, status="closed")

        await bus.emit(
            CoreEvents.LIVE_ENDED,
            LiveEndedPayload(live_session_id=sid, kind=SESSION_KIND_LIVE, ended_at_ms=1_020_000),
            source="test",
        )
        await bus.wait_until_drained()

        ok = await bridge.push_settled_session(
            sid,
            archive_repo=db.archive,
            commitments_repo=db.commitments,
            sessions_repo=db.sessions,
            game_player_repo=db.game_players,
            save_id="save-ocean-1",
        )
        assert ok is False
        assert pending_path.exists()

        # 服务端恢复后调用 replay_pending_checkins 补推成功并清理队列文件
        replayed = await bridge.replay_pending_checkins()
        assert replayed == 1
        assert not pending_path.exists()

        sent_cid, sent_payload = client.checkins[-1]
        assert sent_cid == "chk-101"
        assert sent_payload["discarded"] is False
        assert sent_payload["overview"] == "今晚建成了海岸新灯塔"
        assert len(sent_payload["archive_lines"]) == 1
        assert sent_payload["archive_lines"][0]["title"] == "在MC海岸边建成了新灯塔"
        assert any(p["anchor_id"] == 9 and p["status"] == "closed" for p in sent_payload["promises"])
        assert any(p["content"] == "下次再去扩建码头" and p["status"] == "open" for p in sent_payload["promises"])
        # 仅保留未请求遗忘的玩家互动，遗忘玩家绝不外泄
        assert len(sent_payload["interactions"]) == 1
        assert sent_payload["interactions"][0]["display_name"] == "老熟人甲"
        await bridge.stop()
    finally:
        await db.close()


async def test_offline_degrades_gracefully_without_exceptions(tmp_path: Path) -> None:
    bus = EventBus()
    client = _StubMaiGOClient()
    client.fail_checkout = ConnectionError("connection refused")
    bridge = MaiGOBridge(event_bus=bus, client=client, retry_delays_ms=())
    bridge.setup()
    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=99, kind=SESSION_KIND_LIVE, started_at_ms=5_000),
            source="test",
        )
        await bus.wait_until_drained()
        assert bridge.checkout_id == ""
        assert bridge.render_brief_extension() == ""
        assert bridge.virtual_cards() == []
        assert bridge.lookup_person_cue("bilibili", "20002") is None
        assert await bridge.flush_heartbeat() is False
        assert await bridge.push_settled_session(99) is False
    finally:
        await bridge.stop()


async def test_main_wiring_builds_optional_bridge(tmp_path: Path) -> None:
    bus = EventBus()
    assert app_main._build_maigo_bridge(bus, {}) is None

    db = SQLiteDatabase(tmp_path / "w.db")
    await db.initialize()
    try:
        bridge = app_main._build_maigo_bridge(
            bus,
            {"maigo_sync": {"enabled": True, "url": "http://127.0.0.1:8768", "token": "t1"}},
            database=db,
        )
        assert bridge is not None
        await bridge.stop()
    finally:
        await db.close()


async def test_startup_backfill_high_signal_idempotent_and_resumable(tmp_path: Path) -> None:
    db_file = tmp_path / "amaidesu_hist.db"
    state_file = tmp_path / "maigo_backfill_state.json"
    now_val = 1_795_660_000_000
    recent_t = now_val - 2 * 86_400_000

    db = SQLiteDatabase(db_file)
    await db.initialize()
    try:
        live_sid = await db.sessions.insert_live_session(
            stream_id="live-1", platform="bilibili", kind=SESSION_KIND_LIVE, started_at_ms=recent_t, title="主线建家"
        )
        rehearsal_sid = await db.sessions.insert_live_session(
            stream_id="reh-1",
            platform="bilibili",
            kind=SESSION_KIND_REHEARSAL,
            started_at_ms=recent_t + 100,
            title="彩排",
        )
        chat_id = await db.chat.insert_live_chat(
            live_session_id=live_sid,
            timestamp_ms=recent_t + 50,
            sender_role="assistant",
            content="高炉终于亮起来了！周末一定铺完铁路。",
            message_type="speech",
        )
        reh_chat_id = await db.chat.insert_live_chat(
            live_session_id=rehearsal_sid,
            timestamp_ms=recent_t + 150,
            sender_role="assistant",
            content="彩排测试句",
            message_type="speech",
        )
        await db.archive.insert_line(
            live_session_id=live_sid,
            text="在海边建成高炉仓库",
            quote="高炉终于亮起来了！",
            evidence_chat_id=chat_id,
            game="minecraft",
            save_id="save-ocean-1",
            save_name="海洋一号档",
            highlight=True,
        )
        await db.archive.insert_line(
            live_session_id=rehearsal_sid,
            text="彩排测试句子不能进入历史回填",
            quote="测试",
            evidence_chat_id=reh_chat_id,
            game="minecraft",
            save_id="save-ocean-1",
        )
        await db.commitments.insert_commitment(
            what="周末继续铺设铁路",
            quote="周末一定铺完",
            evidence_chat_id=chat_id,
            created_session_id=live_sid,
            save_id="save-ocean-1",
            now_ms_value=recent_t + 200,
        )
        await db.execute(
            "INSERT INTO viewers (id, platform, account_id, user_name, name_seen_ms, last_active_ms) VALUES (77, 'bilibili', 'u-77', '观众丙', ?, ?)",
            (recent_t, recent_t),
        )
        await db.commitments.insert_commitment(
            what="私下答应某观众的悄悄话",
            quote="悄悄话",
            evidence_chat_id=chat_id,
            viewer_id=77,
            created_session_id=live_sid,
            now_ms_value=recent_t + 250,
        )
        p_keep = await db.game_players.meet_player(
            game="minecraft", uuid="uuid-keep-100", name="好友甲", live_session_id=live_sid, at_ms=recent_t + 300
        )
        p_forget = await db.game_players.meet_player(
            game="minecraft", uuid="uuid-forget-200", name="遗忘玩家乙", live_session_id=live_sid, at_ms=recent_t + 350
        )
        await db.execute("UPDATE game_players SET forget_requested_at_ms=? WHERE id=?", (recent_t + 360, p_forget.id))
        await db.game_players.add_interaction(
            player_id=p_keep.id,
            live_session_id=live_sid,
            kind="gift",
            fact={"item": "面包", "count": 2},
            at_ms=recent_t + 400,
        )
        await db.game_players.add_interaction(
            player_id=p_forget.id,
            live_session_id=live_sid,
            kind="gift",
            fact={"item": "苹果", "count": 1},
            at_ms=recent_t + 450,
        )
        await db.sessions.update_live_session_overview(
            live_session_id=live_sid,
            overview="今晚在海边建好了高炉仓库并挖出了第一批矿石。",
            updated_at_ms=recent_t + 500,
        )
        await db.events.insert_game_event(
            live_session_id=live_sid,
            game="minecraft",
            event_type="milestone",
            message="解锁成就：高炉试运行成功",
            timestamp_ms=recent_t + 120,
            save_id="save-ocean-1",
            save_name="海洋一号档",
        )
        await db.execute(
            "INSERT INTO timeline_summary (live_session_id, start_ms, end_ms, summary) VALUES (?, ?, ?, ?)",
            (live_sid, recent_t + 10, recent_t + 200, "前半小时铺铁轨，后半小时建高炉仓库"),
        )
        await db.execute(
            "INSERT INTO game_work_docs (game, save_id, save_name, notebook, todos, pinned_rules, updated_session_id, updated_at_ms) "
            "VALUES ('minecraft', 'save-ocean-1', '海洋一号档', '高炉配料表与红石线路笔记', '[{\"content\":\"补矿车\",\"status\":\"pending\"}]', '[]', ?, ?)",
            (live_sid, recent_t + 480),
        )
        await db.execute(
            "INSERT INTO game_creatures (game, world_key, creature_key, type, kind, name, nickname, updated_at_ms) "
            "VALUES ('minecraft', 'save-ocean-1', 'uuid:dog-1', 'wolf', 'wolf', '狼', '小花', ?)",
            (recent_t + 490,),
        )
        await db.execute(
            "INSERT INTO game_social_facts (game, save_id, save_name, kind, text, quote, status, created_session_id, created_at_ms, updated_at_ms) "
            "VALUES ('minecraft', 'save-ocean-1', '海洋一号档', 'members', '好友甲负责照看海岸农场', '我来看着农场', 'active', ?, ?, ?)",
            (live_sid, recent_t + 460, recent_t + 460),
        )
    finally:
        await db.close()

    bus = EventBus()
    client = _StubMaiGOClient()
    client.fail_post_after = 0
    bridge = MaiGOBridge(
        event_bus=bus,
        client=client,
        backfill_db_path=db_file,
        backfill_state_path=state_file,
        backfill_on_startup=True,
        backfill_days=30,
        backfill_batch_interval_ms=0,
        clock_ms=lambda: now_val,
    )
    # 首次服务端离线：失败后不写完成状态，留待下次重启续传
    assert await bridge.run_startup_backfill() == 0
    assert not state_file.exists()

    client.fail_post_after = None
    sent = await bridge.run_startup_backfill()
    assert sent >= 6
    assert state_file.exists()

    # L1 仅推送高信噪比档案行与公开承诺，玩家互动已分流至 L2 知识图谱不再污染 L1
    all_events = [ev for batch in client.posted_batches for ev in batch]
    summaries = [ev["summary"] for ev in all_events]
    assert "在海边建成高炉仓库" in summaries
    assert "周末继续铺设铁路" in summaries
    assert not any("彩排测试" in s or "某观众" in s or "遗忘玩家" in s for s in summaries)

    # L0 原件书库包含场次 overview、game_events、timeline_summary、遮名主播原话、work_docs 与 creatures
    all_arc_records = [rec for batch in client.archive_batches for rec in (batch.get("records") or ())]
    rec_types = {rec["record_type"] for rec in all_arc_records}
    assert {"archive_line", "game_event", "timeline_chunk", "speech", "work_doc", "creature"} <= rec_types
    assert any(
        batch.get("episode", {}).get("overview", "").startswith("今晚在海边建好了高炉仓库")
        for batch in client.archive_batches
        if batch.get("episode")
    )
    assert any("高炉试运行成功" in rec["content"] for rec in all_arc_records)
    assert any("高炉配料表" in rec["content"] for rec in all_arc_records)
    assert any("小花" in rec["title"] for rec in all_arc_records)

    # L2 实体关系图谱包含现行社交事实与带真实物品事实的非遗忘玩家互动（严禁遗忘玩家入库）
    all_relations = [rel for batch in client.knowledge_batches for rel in (batch.get("relations") or ())]
    facts = [rel["fact_text"] for rel in all_relations]
    assert any("照看海岸农场" in f for f in facts)
    assert any("好友甲" in f and "面包" in f for f in facts)
    assert not any("遗忘玩家" in f for f in facts)

    # 再次运行保持幂等，不再重复推送已标记完成的场次、原件与关系
    assert await bridge.run_startup_backfill() == 0

    # 验证场中跨端联邦回忆：保留 QQ群/B站 命中并剔除本地直播间回声
    client.recall_response = {
        "ok": True,
        "strong_hit": True,
        "hits": [
            {
                "item_type": "event",
                "item_id": 11,
                "text": "[2026-11-25 · QQ群] 群里答应大家做灯塔",
                "occurred_at_ms": now_val,
            },
            {
                "item_type": "record",
                "item_id": 12,
                "text": "[2026-11-24 · 直播《建家》] 本地直播原话",
                "occurred_at_ms": now_val,
            },
        ],
    }
    fed_hits = await bridge.recall_cross_branch("灯塔", limit=3)
    assert len(fed_hits) == 1
    assert "QQ群" in fed_hits[0]["text"]
    await bridge.stop()


async def test_startup_backfill_chunked_archive_binds_external_episode_id(tmp_path: Path) -> None:
    """单场 L0 原件超过单批上限拆片推送时，第 2 片及之后必须附带 external_episode_id。"""
    db_file = tmp_path / "amaidesu_chunk.db"
    state_file = tmp_path / "backfill_chunk_state.json"
    now_val = 1_800_000_000_000
    recent_t = now_val - 3_600_000

    db = SQLiteDatabase(db_file)
    await db.initialize()
    try:
        sid = await db.sessions.insert_live_session(
            stream_id="live-chunk-1",
            platform="bilibili",
            kind=SESSION_KIND_LIVE,
            started_at_ms=recent_t,
            title="分片绑定测试场",
        )
        for idx in range(5):
            await db.events.insert_game_event(
                live_session_id=sid,
                game="minecraft",
                event_type="milestone",
                message=f"分片测试事件第 {idx + 1} 条",
                timestamp_ms=recent_t + idx * 1000,
                save_id="save-chunk-1",
                save_name="测试档",
            )
    finally:
        await db.close()

    client = _StubMaiGOClient()
    backfiller = StartupHistoryBackfiller(
        db_path=db_file,
        state_path=state_file,
        client=client,
        branch_id="amaidesu",
        since_days=30,
        batch_size=2,
        batch_interval_ms=0,
        clock_ms=lambda: now_val,
    )
    sent = await backfiller.run()
    assert sent >= 5
    # 5 条游戏事件按 batch_size=2 拆成 3 片：第 1 片传完整 episode，第 2/3 片携带 external_episode_id
    assert len(client.archive_batches) == 3
    assert client.archive_batches[0].get("episode") is not None
    assert client.archive_batches[0]["episode"]["external_episode_id"] == str(sid)
    assert "external_episode_id" not in client.archive_batches[0]
    for follow_batch in client.archive_batches[1:]:
        assert follow_batch.get("episode") is None
        assert follow_batch.get("external_episode_id") == str(sid)


@pytest.mark.parametrize(
    ("exc_factory", "expected_suffix"),
    [
        (lambda: TimeoutError(), "TimeoutError"),
        (lambda: TimeoutError("   "), "TimeoutError"),
        (lambda: RuntimeError("连接重置"), "RuntimeError: 连接重置"),
    ],
)
async def test_bridge_exception_warnings_include_class_name_without_trailing_colon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exc_factory: Any,
    expected_suffix: str,
) -> None:
    """异常消息为空串（如 TimeoutError/httpx.ReadTimeout）时告警日志带出异常类名且不留悬空冒号。"""
    warnings: List[str] = []

    def _capture_warning(msg: str, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        warnings.append(str(msg))

    monkeypatch.setattr(maigo_bridge_mod.logger, "warning", _capture_warning)

    bus = EventBus()
    client = _StubMaiGOClient()
    pending_path = tmp_path / "pending.jsonl"
    bridge = MaiGOBridge(
        event_bus=bus,
        client=client,
        pending_log_path=pending_path,
        retry_delays_ms=(),
        clock_ms=lambda: 20_000,
    )
    try:
        # 1) 开播签出失败
        client.fail_checkout = exc_factory()
        assert (
            await bridge.ensure_checked_out(
                LiveStartedPayload(live_session_id=88, kind=SESSION_KIND_LIVE, started_at_ms=10_000)
            )
            is False
        )
        assert any("MaiGO 开播签出失败" in line and line.endswith(f": {expected_suffix}") for line in warnings), (
            warnings
        )

        # 2) 正常签出后验证心跳失败、下播签入失败与离线补推失败日志均携带异常类名且无悬空冒号
        warnings.clear()
        client.fail_checkout = None
        assert (
            await bridge.ensure_checked_out(
                LiveStartedPayload(live_session_id=89, kind=SESSION_KIND_LIVE, started_at_ms=11_000)
            )
            is True
        )

        client.fail_heartbeat = exc_factory()
        assert await bridge.flush_heartbeat() is False
        assert any("MaiGO 心跳上报失败" in line and line.endswith(f": {expected_suffix}") for line in warnings), (
            warnings
        )

        warnings.clear()
        client.fail_checkin_times = 1
        client.checkin_error = exc_factory()
        assert await bridge.push_settled_session(89) is False
        assert any(
            "MaiGO 下播签入失败（checkout=chk-101）" in line and line.endswith(f": {expected_suffix}")
            for line in warnings
        ), warnings
        assert pending_path.exists()

        warnings.clear()
        client.fail_checkin_times = 1
        client.checkin_error = exc_factory()
        assert await bridge.replay_pending_checkins() == 0
        assert any("MaiGO 补推签入失败" in line and line.endswith(f": {expected_suffix}") for line in warnings), (
            warnings
        )
    finally:
        await bridge.stop()


def test_format_brief_extension_rewrites_promise_tags_and_deduplicates_self_branch_echoes() -> None:
    """跨端简报改写 `[约#N]` 为 `[跨端约#N]`、滤除本端回流约定并去重，同时保留真正异端约定且不误伤日常数字。"""
    stable_brief = (
        "# 今日动态\n"
        "- 下午逛了约 5 分钟社区，提到约#88 的闲聊话题。\n\n"
        "【跨端还惦记的约定】\n"
        "- [约#15] 整理树场苗槽\n"
        "- [约#16] 砌菜地墙（来自amaidesu）\n"
        "- [约#32] 周末在群里发新歌单（来自maibot）\n"
    )
    open_promises = [
        CachedPromiseEntry(anchor_id=15, content="整理树场苗槽", status="open", source_branch="amaidesu"),
        CachedPromiseEntry(anchor_id=16, content="砌菜地墙", status="open", source_branch="amaidesu"),
        CachedPromiseEntry(anchor_id=32, content="周末在群里发新歌单", status="open", source_branch="maibot"),
        CachedPromiseEntry(anchor_id=41, content="下周和群友联机挖矿", status="open", source_branch="maibot"),
    ]

    rendered = format_brief_extension(
        stable_brief=stable_brief,
        open_promises=open_promises,
        save_brief="存档备注提到 [约#91] 待跟进",
        current_branch_id="amaidesu",
    )

    # 绝不残留裸 `[约#N]` 或 `约#N`，且日常“约 5 分钟”原样保留
    assert "[约#" not in rendered
    assert "约#15" not in rendered and "约#16" not in rendered
    assert "约 5 分钟" in rendered
    assert "跨端约#88" in rendered
    assert "[跨端约#32] 周末在群里发新歌单（来自maibot）" in rendered
    assert "[跨端约#91]" in rendered
    # 本端同步回流的 amaidesu 约定在两段均被剔除
    assert "整理树场苗槽" not in rendered
    assert "砌菜地墙" not in rendered
    # 已在 stable_brief 出现的异端约#32 不在【跨端还惦记的公开约定】里重复出现，仅补挂未出现过的约#41
    assert rendered.count("周末在群里发新歌单") == 1
    assert "【跨端还惦记的公开约定】" in rendered
    assert "- 跨端约#41（来自maibot）：下周和群友联机挖矿" in rendered

    # 若【跨端还惦记的约定】下全部条目均为本端回流，空分段标题也一并清理
    only_self = format_brief_extension(
        stable_brief="# 今日动态\n- 逛了一圈。\n\n【跨端还惦记的约定】\n- [约#15] 整理树场苗槽\n",
        open_promises=[
            CachedPromiseEntry(anchor_id=15, content="整理树场苗槽", status="open", source_branch="amaidesu")
        ],
        save_brief="",
        current_branch_id="amaidesu",
    )
    assert only_self == "# 今日动态\n- 逛了一圈。"

    # 本端有“修建新木屋”时，明确标注异端来源或编号属已知异端的同词约定绝不因模糊子串被误删
    overlapping_other = format_brief_extension(
        stable_brief=(
            "【跨端还惦记的约定】\n"
            "- [约#15] 修建新木屋\n"
            "- [约#32] 周末群里发修建新木屋的截图（来自maibot）\n"
            "- [约#33] 分享修建新木屋设计草图\n"
        ),
        open_promises=[
            CachedPromiseEntry(anchor_id=15, content="修建新木屋", status="open", source_branch="amaidesu"),
            CachedPromiseEntry(
                anchor_id=32, content="周末群里发修建新木屋的截图", status="open", source_branch="maibot"
            ),
            CachedPromiseEntry(anchor_id=33, content="分享修建新木屋设计草图", status="open", source_branch="maibot"),
        ],
        save_brief="",
        current_branch_id="amaidesu",
    )
    assert "[跨端约#15]" not in overlapping_other
    assert "[跨端约#32] 周末群里发修建新木屋的截图（来自maibot）" in overlapping_other
    assert "[跨端约#33] 分享修建新木屋设计草图" in overlapping_other
    assert "【跨端还惦记的公开约定】" not in overlapping_other


async def test_bridge_filters_self_branch_promises_in_brief_but_retains_anchor_for_checkin(tmp_path: Path) -> None:
    """桥接层签出扩展简报滤掉本端回流约定，但内存仍保留其 anchor_id 以支持下播签入回写结案。"""
    db = SQLiteDatabase(tmp_path / "anchor_retain.db")
    await db.initialize()
    try:
        sid = await db.sessions.insert_live_session(
            stream_id="9002", platform="bilibili", started_at_ms=2_000_000, title="树场整理"
        )
        cid = await db.commitments.insert_commitment(
            what="整理树场苗槽",
            quote="下次整理树场苗槽",
            created_session_id=sid - 1 if sid > 1 else None,
            now_ms_value=1_500_000,
        )
        assert cid is not None
        await db.commitments.close_commitment(commitment_id=cid, close_quote="整理完了", updated_at_ms=2_010_000)

        bus = EventBus()
        client = _StubMaiGOClient(
            checkout_response={
                "checkout_id": "chk-self-echo",
                "brief_etag": "etag-self",
                "stable_brief": "【跨端还惦记的约定】\n- [约#15] 整理树场苗槽\n- [约#32] 周末在群里发新歌单",
                "live_context": "",
                "open_promises": [
                    {"id": 15, "content": "整理树场苗槽", "status": "open", "source_branch": "amaidesu"},
                    {"id": 32, "content": "周末在群里发新歌单", "status": "open", "source_branch": "maibot"},
                ],
                "topic_cards": [],
                "person_directory": [],
                "save_brief": "",
            }
        )
        bridge = MaiGOBridge(event_bus=bus, client=client, retry_delays_ms=(), clock_ms=lambda: 2_020_000)
        try:
            assert (
                await bridge.ensure_checked_out(
                    LiveStartedPayload(live_session_id=sid, kind=SESSION_KIND_LIVE, started_at_ms=2_000_000)
                )
                is True
            )
            ext = bridge.render_brief_extension()
            assert "整理树场苗槽" not in ext
            assert "[跨端约#32]" in ext
            assert ext.count("周末在群里发新歌单") == 1

            assert await bridge.push_settled_session(sid, commitments_repo=db.commitments) is True
            _, payload = client.checkins[-1]
            assert any(p["anchor_id"] == 15 and p["status"] == "closed" for p in payload["promises"])
        finally:
            await bridge.stop()
    finally:
        await db.close()


@pytest.mark.parametrize(
    ("exc_factory", "expected_suffix"),
    [
        (lambda: TimeoutError(), "TimeoutError"),
        (lambda: TimeoutError("   "), "TimeoutError"),
        (lambda: RuntimeError("连接重置"), "RuntimeError: 连接重置"),
    ],
)
async def test_backfill_exception_warnings_include_class_name_without_trailing_colon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exc_factory: Any,
    expected_suffix: str,
) -> None:
    """历史回填 L1 / L0 场次 / L0 世界笔记 / L2 异常日志统一带出异常类名，空消息异常不留悬空冒号。"""
    db_file = tmp_path / "backfill_exc.db"
    state_file = tmp_path / "backfill_exc_state.json"
    now_val = 1_800_000_000_000
    recent_t = now_val - 3_600_000

    db = SQLiteDatabase(db_file)
    await db.initialize()
    try:
        sid = await db.sessions.insert_live_session(
            stream_id="live-exc-1",
            platform="bilibili",
            kind=SESSION_KIND_LIVE,
            started_at_ms=recent_t,
            title="异常日志测试场",
        )
        chat_id = await db.chat.insert_live_chat(
            live_session_id=sid,
            timestamp_ms=recent_t + 100,
            sender_role="assistant",
            content="今天把营地工作台整理好了",
            message_type="speech",
        )
        await db.archive.insert_line(
            live_session_id=sid,
            text="整理营地工作台",
            quote="今天把营地工作台整理好了",
            evidence_chat_id=chat_id,
            game="minecraft",
            save_id="save-exc",
        )
        await db.execute(
            "INSERT INTO game_work_docs (game, save_id, save_name, notebook, todos, pinned_rules, updated_session_id, updated_at_ms) "
            "VALUES ('minecraft', 'save-exc', '异常测试档', '测试笔记内容', '[]', '[]', ?, ?)",
            (sid, recent_t + 200),
        )
        await db.execute(
            "INSERT INTO game_social_facts (game, save_id, save_name, kind, text, quote, status, created_session_id, created_at_ms, updated_at_ms) "
            "VALUES ('minecraft', 'save-exc', '异常测试档', 'members', '玩家甲负责仓库', '我管仓库', 'active', ?, ?, ?)",
            (sid, recent_t + 300, recent_t + 300),
        )
    finally:
        await db.close()

    warnings: List[str] = []

    def _capture_warning(msg: str, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        warnings.append(str(msg))

    monkeypatch.setattr(maigo_backfill_mod.logger, "warning", _capture_warning)

    client = _StubMaiGOClient()
    backfiller = StartupHistoryBackfiller(
        db_path=db_file,
        state_path=state_file,
        client=client,
        branch_id="amaidesu",
        since_days=30,
        batch_size=10,
        batch_interval_ms=0,
        clock_ms=lambda: now_val,
    )

    # 1) L1 暂停告警
    client.fail_post_after = 0
    client.fail_post_error = exc_factory()
    assert await backfiller.run() == 0
    assert any("MaiGO 启动历史回填暂停" in line and line.endswith(f": {expected_suffix}") for line in warnings), (
        warnings
    )

    # 2) L0 场次原件暂停告警
    warnings.clear()
    client.fail_post_after = None
    client.fail_post_error = None
    client.fail_archive_error = exc_factory()
    await backfiller.run()
    assert any("MaiGO L0 原件书库回填暂停" in line and line.endswith(f": {expected_suffix}") for line in warnings), (
        warnings
    )

    # 3) L0 世界笔记/同伴名册暂停告警
    warnings.clear()
    client.fail_archive_error = None
    client.fail_world_archive_error = exc_factory()
    await backfiller.run()
    assert any(
        "MaiGO 世界笔记/同伴名册回填暂停" in line and line.endswith(f": {expected_suffix}") for line in warnings
    ), warnings

    # 4) L2 实体关系暂停告警
    warnings.clear()
    client.fail_world_archive_error = None
    client.fail_knowledge_error = exc_factory()
    await backfiller.run()
    assert any("MaiGO L2 实体关系回填暂停" in line and line.endswith(f": {expected_suffix}") for line in warnings), (
        warnings
    )
