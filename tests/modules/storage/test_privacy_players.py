"""删游戏玩家：级联删除范围、名字换成"一位玩家"、进不记录名单后再出现不记；删观众连带运营者确认过的玩家。"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator, Dict, Tuple

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameChatPayload
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.game_player_ledger import GamePlayerLedger
from src.modules.storage.privacy import PrivacyService

_ALEX = "uuid-alex"


@pytest.fixture()
async def wired(tmp_path: Path) -> AsyncIterator[Tuple[SQLiteDatabase, PrivacyService]]:
    store = SQLiteDatabase(tmp_path / "data" / "players.db")
    await store.initialize()
    privacy = PrivacyService(store.manager, db_path=store.db_path, key_path=tmp_path / "secrets" / "forget.key")
    await privacy.initialize()
    try:
        yield store, privacy
    finally:
        await privacy.close()
        await store.close()


async def _alex_scene(store: SQLiteDatabase) -> Dict[str, int]:
    """Alex（以前叫 Steve）：打过招呼、公屏说过话、她回应过他也答应过他事，游戏事件里也提到他。"""
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    repo = store.game_players
    await repo.meet_player(game="minecraft", uuid=_ALEX, name="Steve", live_session_id=session, at_ms=1_010)
    alex = await repo.meet_player(game="minecraft", uuid=_ALEX, name="Alex", live_session_id=session, at_ms=1_020)
    await repo.record_greeting(player_id=alex.id, live_session_id=session, actions=["挥手"], at_ms=1_030)
    await repo.add_game_chat(
        live_session_id=session, game="minecraft", direction="player", content="来切磋", player_id=alex.id, at_ms=1_040
    )
    mine = await repo.add_game_chat(
        live_session_id=session, game="minecraft", direction="streamer", content="Alex 下次带你挖矿", at_ms=1_050
    )
    speech = await store.chat.insert_live_chat(
        live_session_id=session,
        timestamp_ms=1_060,
        sender_role="assistant",
        content="好呀 Steve，哦不，Alex",
        message_type="speech",
        reply_player_id=alex.id,
    )
    await store.events.insert_game_event(
        live_session_id=session,
        game="minecraft",
        event_type="milestone",
        message="帮 Alex 修好了房子",
        timestamp_ms=1_070,
    )
    promise = await store.commitments.insert_commitment(what="带他挖矿", created_session_id=session, now_ms_value=1_080)
    assert promise is not None
    with store.manager.transaction() as conn:
        conn.execute("UPDATE commitments SET target_player_id=? WHERE id=?", (alex.id, promise))
    return {"session": session, "alex": alex.id, "mine": mine, "speech": speech, "promise": promise}


async def test_deleting_a_player_scrubs_him_and_keeps_him_forgotten(
    wired: Tuple[SQLiteDatabase, PrivacyService],
) -> None:
    store, privacy = wired
    ids = await _alex_scene(store)

    assert await privacy.delete_player(player_id=ids["alex"]) is True
    assert await store.game_players.get_player(player_id=ids["alex"]) is None
    with store.manager.transaction() as conn:
        for table in ("game_player_interactions", "commitments"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table  # noqa: S608 测试常量表名
        chat = [(row["direction"], row["content"]) for row in conn.execute("SELECT direction, content FROM game_chat")]
        speech = conn.execute("SELECT content, reply_player_id FROM live_chat WHERE id=?", (ids["speech"],)).fetchone()
        event = conn.execute("SELECT message FROM game_events").fetchone()
    # 他说的那句随他删；她打在公屏、播出的话和游戏事件里他的名字（含以前的名字）换成"一位玩家"
    assert chat == [("streamer", "一位玩家 下次带你挖矿")]
    assert (speech["content"], speech["reply_player_id"]) == ("好呀 一位玩家，哦不，一位玩家", None)
    assert event["message"] == "帮 一位玩家 修好了房子"
    assert await privacy.is_player_forgotten(game="minecraft", uuid=_ALEX) is True
    assert await privacy.delete_player(player_id=ids["alex"]) is False, "删过的再删一次：没有这一行"

    # 他再出现：落库器不再给他建档，他那句只占一行位置
    bus = EventBus()
    ledger = GamePlayerLedger(bus, store.game_players, privacy)
    await ledger.start()
    try:
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                content="我又来了",
                sender="Alex",
                sender_id=_ALEX,
                sender_online=True,
                live_session_id=ids["session"],
            ),
            source="test",
        )
        await bus.wait_until_drained()
    finally:
        await ledger.stop()
        await bus.cleanup()
    assert await store.game_players.find_player(game="minecraft", uuid=_ALEX) is None
    lines = await store.game_players.list_session_game_chat(live_session_id=ids["session"])
    assert [(line.direction, line.content) for line in lines][-1] == ("other", "")


async def test_deleting_a_viewer_takes_the_confirmed_player_too(
    wired: Tuple[SQLiteDatabase, PrivacyService],
) -> None:
    store, privacy = wired
    ids = await _alex_scene(store)
    viewer = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_alex",
        user_name="阿杰",
        timestamp_ms=1_000,
        name_match_window_ms=0,
    )
    stranger = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-other", name="Bob", live_session_id=ids["session"], at_ms=1_100
    )
    await store.game_players.link_viewer(viewer_id=viewer.viewer_id, player_id=ids["alex"], at_ms=2_000)

    assert await privacy.delete_viewer(viewer_id=viewer.viewer_id, add_to_forget_list=True) is True
    assert await store.game_players.get_player(player_id=ids["alex"]) is None, "确认过的关联玩家一并删"
    assert await store.game_players.get_player(player_id=stranger.id) is not None, "没关联的玩家不动"
    assert await privacy.is_player_forgotten(game="minecraft", uuid=_ALEX) is True


async def _say(bus: EventBus, session_id: int, content: str, at_ms: int) -> None:
    """Alex 在公屏说一句（核对过在线、带 UUID）。"""
    await bus.emit(
        CoreEvents.GAME_CHAT_RECEIVED,
        GameChatPayload(
            game="minecraft",
            kind="player",
            content=content,
            sender="Alex",
            sender_id=_ALEX,
            sender_online=True,
            live_session_id=session_id,
            timestamp_ms=at_ms,
        ),
        source="test",
    )
    await bus.wait_until_drained()


async def test_forget_me_typed_in_game_chat_has_the_same_grace_and_takes_linked_viewers(
    wired: Tuple[SQLiteDatabase, PrivacyService],
) -> None:
    """玩家在公屏发"忘了我"：立即停止记录；宽限期内撤回就恢复；下播后清理删掉他和确认过的关联观众。"""
    store, privacy = wired
    ids = await _alex_scene(store)
    bus = EventBus()
    ledger = GamePlayerLedger(bus, store.game_players, privacy)
    await ledger.start()
    try:
        await _say(bus, ids["session"], "忘了我", 5_000)
        alex = await store.game_players.get_player(player_id=ids["alex"])
        assert alex is not None and alex.forget_requested_at_ms == 5_000
        await _say(bus, ids["session"], "我刚才说了什么", 5_100)
        # 撤回：清掉标记、移出名单，他接下来的话照常记
        await _say(bus, ids["session"], "算了", 5_200)
        alex = await store.game_players.get_player(player_id=ids["alex"])
        assert alex is not None and alex.forget_requested_at_ms is None
        assert await privacy.is_player_forgotten(game="minecraft", uuid=_ALEX) is False
        await _say(bus, ids["session"], "还是记着我吧", 5_300)
        lines = await store.game_players.list_session_game_chat(live_session_id=ids["session"])
        assert [(line.direction, line.content) for line in lines][-4:] == [
            ("other", ""),
            ("other", ""),
            ("other", ""),
            ("player", "还是记着我吧"),
        ], "命令那句、宽限期里的话、撤回那句都只占位"

        # 再发一次，这回不撤回；关联的观众一起删
        viewer = await store.viewers.resolve_viewer(
            platform="bilibili",
            id_kind="account",
            external_id="u_alex",
            user_name="阿杰",
            timestamp_ms=1_000,
            name_match_window_ms=0,
        )
        await store.game_players.link_viewer(viewer_id=viewer.viewer_id, player_id=ids["alex"], at_ms=6_000)
        await _say(bus, ids["session"], "忘了我吧", 6_100)
    finally:
        await ledger.stop()
        await bus.cleanup()

    assert await privacy.execute_due_forget_deletions(now_ms=6_200) == 0, "还没下播、没满 24 小时：不删"
    await store.sessions.close_live_session(live_session_id=ids["session"], ended_at_ms=7_000)
    assert await privacy.execute_due_forget_deletions(now_ms=7_100) == 1
    assert await store.game_players.get_player(player_id=ids["alex"]) is None
    assert await store.viewers.get_viewer(viewer_id=viewer.viewer_id) is None, "确认过的关联观众一起删"
    assert await privacy.is_player_forgotten(game="minecraft", uuid=_ALEX) is True
