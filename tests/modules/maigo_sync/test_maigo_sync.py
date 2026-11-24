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
        self.fail_checkout: Optional[Exception] = None
        self.fail_checkin_times: int = 0
        self.checkin_error: Exception = RuntimeError("MaiGO 离线")
        self.closed = False

    async def checkout(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.checkouts.append(dict(payload))
        if self.fail_checkout is not None:
            raise self.fail_checkout
        return dict(self.checkout_response)

    async def heartbeat(self, checkout_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.heartbeats.append((checkout_id, dict(payload)))
        return {"ok": True, "checkout_id": checkout_id, "consumed_count": len(payload.get("consumed_seed_ids") or [])}

    async def checkin(self, checkout_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.checkins.append((checkout_id, dict(payload)))
        if self.fail_checkin_times > 0:
            self.fail_checkin_times -= 1
            raise self.checkin_error
        return {"ok": True, "checkout_id": checkout_id}

    async def consume_topic(self, topic_id: int, *, branch_id: str) -> Dict[str, Any]:
        return {"ok": True, "topic_id": topic_id, "branch_id": branch_id}

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
