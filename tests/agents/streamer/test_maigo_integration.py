"""主播记忆与话题匣对接 MaiGOBridge 的联动测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agents.streamer.memory.cues import MemoryCueProvider
from src.agents.streamer.talk.deck import TopicDeck
from src.modules.events.event_bus import EventBus
from src.modules.events.payloads.live import LiveStartedPayload
from src.modules.events.payloads.room import RoomMessageUser
from src.modules.maigo_sync import MaiGOBridge
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.talk_card import TalkCard
from src.modules.storage.privacy import PrivacyService
from src.modules.types.session_kind import SESSION_KIND_LIVE
from tests.modules.storage.helpers import make_room_message


class _LocalMemoryCardStore:
    """仅记录本地卡片调用的内存 Store，用于验证负数跨端卡不写入本地卡池。"""

    def __init__(self, cards: List[TalkCard]) -> None:
        self.cards = {c.id: c for c in cards}
        self.mark_used_calls: List[int] = []

    async def list_cards(self, *, kind: Optional[str] = None, enabled_only: bool = False) -> List[TalkCard]:
        rows = [c for c in self.cards.values() if kind is None or c.kind == kind]
        return [c for c in rows if c.enabled or not enabled_only]

    async def get_card(self, card_id: int) -> Optional[TalkCard]:
        return self.cards.get(card_id)

    async def mark_used(self, card_id: int, *, session_id: Optional[int], at_ms: int) -> bool:
        self.mark_used_calls.append(card_id)
        return card_id in self.cards


class _FakeMaiGOClient:
    def __init__(self) -> None:
        self.heartbeats: List[Dict[str, Any]] = []

    async def checkout(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        del payload
        return {
            "checkout_id": "chk-stream-1",
            "brief_etag": "etag-s1",
            "stable_brief": "# 麦麦的今日生活（截至目前）\n- 下午：在B站刷到手工视频并评论。",
            "live_context": "",
            "open_promises": [],
            "topic_cards": [
                {
                    "id": 33,
                    "title": "粘土手工视频",
                    "summary": "下午在B站刷到手工视频并留评",
                    "reaction": "想顺便聊聊翻车经历",
                    "source_branch": "bilibili_ops",
                    "consumed_branches": ["maibot"],
                    "transition_hint": "（群里提过一嘴）",
                }
            ],
            "person_directory": [
                {
                    "person_id": 5,
                    "platform": "bilibili",
                    "external_id": "20002",
                    "display_name": "老熟人甲",
                    "prompt_hint": "老熟人甲：在群里聊过手工视频，也是同一位好友。",
                },
                {
                    "person_id": 5,
                    "platform": "minecraft",
                    "external_id": "00000000-0000-0000-0000-000000000001",
                    "display_name": "老熟人甲",
                    "prompt_hint": "老熟人甲：在群里聊过手工视频，也是游戏好友。",
                },
            ],
            "save_brief": "",
        }

    async def heartbeat(self, checkout_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        del checkout_id
        self.heartbeats.append(dict(payload))
        return {"ok": True}

    async def checkin(self, checkout_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        del checkout_id, payload
        return {"ok": True}

    async def consume_topic(self, topic_id: int, *, branch_id: str) -> Dict[str, Any]:
        del topic_id, branch_id
        return {"ok": True}

    async def close(self) -> None:
        return None


async def test_topic_deck_merges_and_consumes_virtual_maigo_cards() -> None:
    bus = EventBus()
    client = _FakeMaiGOClient()
    bridge = MaiGOBridge(event_bus=bus, client=client, retry_delays_ms=())
    assert await bridge.ensure_checked_out(LiveStartedPayload(live_session_id=1, kind=SESSION_KIND_LIVE))

    local_store = _LocalMemoryCardStore(
        [TalkCard(id=2, kind="episode", title="本地往场趣事", created_at_ms=1, updated_at_ms=1)]
    )
    deck = TopicDeck(
        local_store,
        session_id_fn=lambda: 1,
        clock=lambda: 1_000,
        extra_cards_provider=bridge.virtual_cards,
        on_extra_card_used=bridge.mark_card_used,
    )
    preset = await deck.refresh(should_preset=True)
    # 负数编号的跨端话头排在正数本地 episode 卡前面
    assert [c.id for c in preset] == [-33, 2]
    rendered = deck.render()
    assert "[今日见闻] 粘土手工视频" in rendered and "（群里提过一嘴）" in rendered

    chosen = await deck.use_card(-33)
    assert chosen is not None and chosen.id == -33
    assert local_store.mark_used_calls == []
    assert [c.id for c in deck.preset_cards()] == [2]
    assert await bridge.flush_heartbeat() is True
    assert client.heartbeats[0]["consumed_seed_ids"] == [33]
    await bridge.stop()


async def test_memory_cues_cross_channel_person_and_forgetting_guard(tmp_path: Path) -> None:
    db = SQLiteDatabase(tmp_path / "cues_switch.db")
    await db.initialize()
    privacy = PrivacyService(db.manager, db_path=db.db_path, key_path=tmp_path / "k.key", bot_name="麦麦")
    await privacy.initialize()
    bus = EventBus()
    client = _FakeMaiGOClient()
    bridge = MaiGOBridge(event_bus=bus, client=client, retry_delays_ms=())
    now_value = 1_000_000_000_000

    try:
        sid = await db.sessions.insert_live_session(started_at_ms=now_value)
        assert await bridge.ensure_checked_out(
            LiveStartedPayload(live_session_id=sid, kind=SESSION_KIND_LIVE, started_at_ms=now_value)
        )
        provider = MemoryCueProvider(
            commitments_repo=db.commitments,
            chat_repo=db.chat,
            viewer_repo=db.viewers,
            game_player_repo=db.game_players,
            privacy_service=privacy,
            current_session_provider=lambda: sid,
            cross_channel_person_lookup=bridge.lookup_person_cue,
            clock=lambda: now_value,
        )

        # 弹幕发言命中 B 站绑定熟人，返回脱敏线索（不含任何外部数字账号）
        danmaku = make_room_message(
            platform="bilibili",
            user=RoomMessageUser(id="20002", name="老熟人甲"),
            content="主播下午在看啥",
            message_id="msg-1",
            timestamp_ms=now_value,
        )
        cues = await provider.collect(batch=[danmaku])
        assert cues.card_count == 1
        assert "【跨端熟人线索】" in cues.text and "老熟人甲" in cues.text
        assert "20002" not in cues.text

        # 同批再以相同 person_id 的 MC 玩家出现，10 分钟内受冷却共享拦截
        cues_cooldown = await provider.collect(
            batch=[],
            game_players=[("minecraft", "00000000-0000-0000-0000-000000000001")],
        )
        assert cues_cooldown.card_count == 0

        # 超过 10 分钟后由 MC 玩家出场命中第 2 次（达到每场上限）
        now_value += 11 * 60 * 1000
        cues_mc = await provider.collect(
            batch=[],
            game_players=[("minecraft", "00000000-0000-0000-0000-000000000001")],
        )
        assert cues_mc.card_count == 1
        assert "【跨端熟人线索】" in cues_mc.text and "游戏好友" in cues_mc.text

        # 若玩家已被标为宽限删除（忘了我），绝不推出跨端熟人线索
        sid2 = await db.sessions.insert_live_session(started_at_ms=now_value)
        p_row = await db.game_players.meet_player(
            game="minecraft",
            uuid="00000000-0000-0000-0000-000000000001",
            name="老熟人甲",
            live_session_id=sid2,
            at_ms=now_value,
        )
        await db.execute(
            "UPDATE game_players SET forget_requested_at_ms=? WHERE id=?",
            (now_value, p_row.id),
        )
        provider_forget = MemoryCueProvider(
            commitments_repo=db.commitments,
            chat_repo=db.chat,
            viewer_repo=db.viewers,
            game_player_repo=db.game_players,
            privacy_service=privacy,
            current_session_provider=lambda: sid2,
            cross_channel_person_lookup=bridge.lookup_person_cue,
            clock=lambda: now_value,
        )
        blocked = await provider_forget.collect(
            batch=[],
            game_players=[("minecraft", "00000000-0000-0000-0000-000000000001")],
        )
        assert blocked.card_count == 0
    finally:
        await bridge.stop()
        await privacy.close()
        await db.close()
