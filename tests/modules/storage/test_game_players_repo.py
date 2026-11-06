"""游戏里的人仓储：按 UUID 认人、打招呼按场合并、公屏聊天只落公屏、观众关联与改彩排时撤掉互动。"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.game_players import (
    GAME_CHAT_OTHER,
    GAME_CHAT_PLAYER,
    GAME_CHAT_STREAMER,
    INTERACTION_GIFT,
    INTERACTION_GREET,
)


@pytest.fixture()
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    db = SQLiteDatabase(tmp_path / "data" / "players.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


async def _session(store: SQLiteDatabase, started_at_ms: int = 1_000) -> int:
    session_id = await store.sessions.insert_live_session(started_at_ms=started_at_ms, kind="live")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=started_at_ms + 1_000)
    return session_id


async def test_same_uuid_after_a_rename_is_the_same_player(store: SQLiteDatabase) -> None:
    first = await _session(store)
    second = await _session(store, 5_000)
    alex = await store.game_players.meet_player(
        game="minecraft",
        uuid="UUID-A",
        name="Alex",
        live_session_id=first,
        save_id="w1",
        save_name="生存服",
        at_ms=1_100,
    )
    renamed = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-a", name="Steve", live_session_id=second, save_id="w2", at_ms=5_100
    )
    assert renamed.id == alex.id
    assert (renamed.name, renamed.names, renamed.former_names()) == ("Steve", ["Alex", "Steve"], ["Alex"])
    assert [save.save_id for save in renamed.saves] == ["w1", "w2"]
    assert (renamed.first_session_id, renamed.last_session_id, renamed.last_seen_ms) == (first, second, 5_100)


async def test_same_name_with_another_uuid_is_another_player(store: SQLiteDatabase) -> None:
    session_id = await _session(store)
    one = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-1", name="Alex", live_session_id=session_id, at_ms=1_100
    )
    two = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-2", name="Alex", live_session_id=session_id, at_ms=1_200
    )
    assert one.id != two.id
    assert [player.uuid for player in await store.game_players.list_players()] == ["uuid-2", "uuid-1"]
    assert await store.game_players.find_player(game="minecraft", uuid="uuid-3") is None


async def test_greetings_merge_into_one_row_per_session(store: SQLiteDatabase) -> None:
    first = await _session(store)
    second = await _session(store, 5_000)
    alex = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-a", name="Alex", live_session_id=first, at_ms=1_100
    )
    repo = store.game_players
    await repo.record_greeting(player_id=alex.id, live_session_id=first, actions=["蹲起", "挥手"], at_ms=1_200)
    await repo.record_greeting(player_id=alex.id, live_session_id=first, actions=["挥手", "挥手"], at_ms=1_300)
    await repo.record_greeting(player_id=alex.id, live_session_id=second, at_ms=5_200)
    rows = await repo.list_interactions(player_id=alex.id)
    assert [(row.live_session_id, row.kind, row.fact) for row in rows] == [
        (second, INTERACTION_GREET, {"count": 1, "actions": {}}),
        (first, INTERACTION_GREET, {"count": 2, "actions": {"蹲起": 1, "挥手": 2}}),
    ]
    assert rows[1].at_ms == 1_300


async def test_other_interactions_are_one_row_each_and_greetings_go_their_own_way(store: SQLiteDatabase) -> None:
    session_id = await _session(store)
    alex = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-a", name="Alex", live_session_id=session_id, at_ms=1_100
    )
    gift = {"items": [{"item": "minecraft:bread", "count": 3}]}
    await store.game_players.add_interaction(
        player_id=alex.id, live_session_id=session_id, kind=INTERACTION_GIFT, fact=gift, at_ms=1_200
    )
    (row,) = await store.game_players.list_interactions(player_id=alex.id)
    assert (row.kind, row.fact) == (INTERACTION_GIFT, gift)
    for kind in (INTERACTION_GREET, "whisper"):
        with pytest.raises(ValueError):
            await store.game_players.add_interaction(
                player_id=alex.id, live_session_id=session_id, kind=kind, fact={}, at_ms=1_300
            )


async def test_public_chat_keeps_who_said_it_and_unknown_speakers_only_hold_a_place(store: SQLiteDatabase) -> None:
    session_id = await _session(store)
    alex = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-a", name="Alex", live_session_id=session_id, at_ms=1_100
    )
    repo = store.game_players
    await repo.add_game_chat(
        live_session_id=session_id,
        game="minecraft",
        direction=GAME_CHAT_PLAYER,
        content="来切磋",
        player_id=alex.id,
        at_ms=1_200,
    )
    await repo.add_game_chat(
        live_session_id=session_id, game="minecraft", direction=GAME_CHAT_OTHER, content="[Server] 重启", at_ms=1_300
    )
    await repo.add_game_chat(
        live_session_id=session_id, game="minecraft", direction=GAME_CHAT_STREAMER, content="来就来", at_ms=1_400
    )
    lines = await repo.list_session_game_chat(live_session_id=session_id)
    assert [(line.direction, line.player_id, line.content) for line in lines] == [
        (GAME_CHAT_PLAYER, alex.id, "来切磋"),
        (GAME_CHAT_OTHER, None, ""),
        (GAME_CHAT_STREAMER, None, "来就来"),
    ]
    with pytest.raises(ValueError):
        await repo.add_game_chat(
            live_session_id=session_id, game="minecraft", direction=GAME_CHAT_PLAYER, content="谁说的", at_ms=1_500
        )


async def test_a_name_finds_the_player_only_when_one_was_seen_this_session(store: SQLiteDatabase) -> None:
    first = await _session(store)
    second = await _session(store, 5_000)
    alex = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-a", name="Alex", live_session_id=second, at_ms=5_100
    )
    await store.game_players.meet_player(
        game="minecraft", uuid="uuid-s", name="Steve", live_session_id=first, at_ms=1_100
    )
    repo = store.game_players
    assert await repo.session_player_named(game="minecraft", name="Alex", live_session_id=second) == alex.id
    # 别的场次见过的不算；同名两位就不猜
    assert await repo.session_player_named(game="minecraft", name="Steve", live_session_id=second) is None
    await repo.meet_player(game="minecraft", uuid="uuid-a2", name="Alex", live_session_id=second, at_ms=5_200)
    assert await repo.session_player_named(game="minecraft", name="Alex", live_session_id=second) is None


async def test_operator_links_a_viewer_and_can_undo_it(store: SQLiteDatabase) -> None:
    session_id = await _session(store)
    alex = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-a", name="Alex", live_session_id=session_id, at_ms=1_100
    )
    viewer = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="UID_1",
        user_name="Alex",
        timestamp_ms=1_100,
        name_match_window_ms=0,
    )
    # 同名的观众与玩家不会自动对上：没确认之前一条关联都没有
    assert await store.game_players.list_links() == []
    await store.game_players.link_viewer(viewer_id=viewer.viewer_id, player_id=alex.id, at_ms=2_000)
    await store.game_players.link_viewer(viewer_id=viewer.viewer_id, player_id=alex.id, at_ms=3_000)
    (link,) = await store.game_players.list_links(player_id=alex.id)
    assert (link.viewer_id, link.confirmed_at_ms) == (viewer.viewer_id, 3_000)
    assert await store.game_players.unlink_viewer(viewer_id=viewer.viewer_id, player_id=alex.id) is True
    assert await store.game_players.list_links(viewer_id=viewer.viewer_id) == []


async def test_turning_a_session_into_rehearsal_drops_its_interactions(store: SQLiteDatabase) -> None:
    session_id = await _session(store)
    alex = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-a", name="Alex", live_session_id=session_id, at_ms=1_100
    )
    await store.game_players.record_greeting(
        player_id=alex.id, live_session_id=session_id, actions=["挥手"], at_ms=1_200
    )
    said = await store.game_players.add_game_chat(
        live_session_id=session_id,
        game="minecraft",
        direction=GAME_CHAT_PLAYER,
        content="我是这服服主",
        player_id=alex.id,
        at_ms=1_300,
    )
    with store.manager.transaction() as conn:
        conn.execute(
            "INSERT INTO player_candidates(live_session_id, player_id, title, player_chat_id, player_quote,"
            " reply_quote, created_at_ms) VALUES (?, ?, '他是这服服主', ?, '我是这服服主', '好的', 1_400)",
            (session_id, alex.id, said),
        )
    await store.sessions.set_live_session_kind_with_ledger(
        live_session_id=session_id, kind="rehearsal", updated_at_ms=9_000
    )
    # 彩排里的互动与待审自述不进正式的记忆；人还认得，公屏聊天作为原始记录照旧留着
    assert await store.game_players.list_interactions(player_id=alex.id) == []
    assert await store.execute("SELECT id FROM player_candidates") == []
    assert await store.game_players.get_player(player_id=alex.id) is not None
    assert len(await store.game_players.list_session_game_chat(live_session_id=session_id)) == 1
