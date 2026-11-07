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
from src.modules.storage.repos.memory_review import MemoryReviewError


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


async def test_saves_keep_whether_the_server_checks_accounts(store: SQLiteDatabase) -> None:
    """见过他的存档连同那台服务器验不验账号一起记：报了的按最新的写，没报（unknown）不抹掉已知的。"""
    session_id = await _session(store)

    async def meet(save_id: str, mode: str) -> list[tuple[str, str]]:
        player = await store.game_players.meet_player(
            game="minecraft",
            uuid="uuid-a",
            name="Alex",
            live_session_id=session_id,
            save_id=save_id,
            save_online_mode=mode,  # type: ignore[arg-type]
            at_ms=1_100,
        )
        return [(save.save_id, save.online_mode) for save in player.saves]

    assert await meet("w1", "off") == [("w1", "off")]
    assert await meet("w1", "unknown") == [("w1", "off")], "这次没报不抹掉早先报过的"
    assert await meet("w2", "unknown") == [("w1", "off"), ("w2", "unknown")]
    assert await meet("w2", "on") == [("w1", "off"), ("w2", "on")]
    # 早先记下、没有账号验证一项的存档按 unknown 读
    with store.manager.transaction() as conn:
        conn.execute("UPDATE game_players SET saves=? WHERE uuid='uuid-a'", ('[{"save_id": "w3", "save_name": ""}]',))
    (alex,) = await store.game_players.list_players()
    assert [(save.save_id, save.online_mode) for save in alex.saves] == [("w3", "unknown")]


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
            " reply_quote, created_at_ms) VALUES (?, ?, '他是这服服主', ?, '我是这服服主', '好的', 1400)",
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


async def test_self_statements_are_approved_or_rejected_once_and_only_approved_ones_reach_the_card(
    store: SQLiteDatabase,
) -> None:
    """玩家自述：批准可以改标题、驳回留着；审过的不能再审；玩家卡只读批准过的，最近批准的在前，带存档。"""
    session = await _session(store)
    repo = store.game_players
    player = await repo.meet_player(
        game="minecraft", uuid="uuid-jia", name="玩家甲", live_session_id=session, at_ms=1_010
    )
    ids = []
    for index, (content, title) in enumerate(
        (("我在雪山脚下挖了基地", "他在雪山脚下有基地"), ("我的猫叫团子", "他的猫叫团子"), ("我爱钓鱼", "他爱钓鱼"))
    ):
        chat = await repo.add_game_chat(
            live_session_id=session,
            game="minecraft",
            direction=GAME_CHAT_PLAYER,
            content=content,
            player_id=player.id,
            save_id="w1",
            save_name="生存一档",
            at_ms=1_100 + index,
        )
        with store.manager.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO player_candidates(live_session_id, player_id, title, player_chat_id, player_quote,"
                " reply_quote, created_at_ms) VALUES (?, ?, ?, ?, ?, '真好', ?)",
                (session, player.id, title, chat, content, 2_000 + index),
            )
            ids.append(int(cur.lastrowid or 0))

    assert await repo.count_pending_player_candidates() == 3
    first = await repo.decide_player_candidate(
        candidate_id=ids[0], approve=True, at_ms=3_000, title="他的基地在雪山脚下"
    )
    assert (first.status, first.title, first.decided_at_ms) == ("approved", "他的基地在雪山脚下", 3_000)
    await repo.decide_player_candidate(candidate_id=ids[1], approve=True, at_ms=3_100)
    rejected = await repo.decide_player_candidate(candidate_id=ids[2], approve=False, at_ms=3_200)
    assert (rejected.status, rejected.title) == ("rejected", "他爱钓鱼")

    with pytest.raises(MemoryReviewError) as conflict:
        await repo.decide_player_candidate(candidate_id=ids[0], approve=False, at_ms=4_000)
    assert conflict.value.kind == "conflict"
    with pytest.raises(MemoryReviewError) as missing:
        await repo.decide_player_candidate(candidate_id=999, approve=True, at_ms=4_000)
    assert missing.value.kind == "not_found"

    assert await repo.count_pending_player_candidates() == 0
    assert [item.status for item in await repo.list_player_candidates(status="approved")] == ["approved", "approved"]
    statements = await repo.approved_statements(player_id=player.id)
    assert [(item.title, item.save_id, item.save_name) for item in statements] == [
        ("他的猫叫团子", "w1", "生存一档"),
        ("他的基地在雪山脚下", "w1", "生存一档"),
    ]


async def test_approving_a_self_statement_checks_the_title(store: SQLiteDatabase) -> None:
    """批准时改的标题不能为空、不能超过 30 字；没过的保持待审。"""
    session = await _session(store)
    player = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-yi", name="玩家乙", live_session_id=session, at_ms=1_010
    )
    chat = await store.game_players.add_game_chat(
        live_session_id=session,
        game="minecraft",
        direction=GAME_CHAT_PLAYER,
        content="叫我小乙",
        player_id=player.id,
        at_ms=1_100,
    )
    with store.manager.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO player_candidates(live_session_id, player_id, title, player_chat_id, player_quote,"
            " reply_quote, created_at_ms) VALUES (?, ?, '他希望被叫作小乙', ?, '叫我小乙', '好', 2000)",
            (session, player.id, chat),
        )
        candidate_id = int(cur.lastrowid or 0)
    for title in ("  ", "长" * 31):
        with pytest.raises(MemoryReviewError) as invalid:
            await store.game_players.decide_player_candidate(
                candidate_id=candidate_id, approve=True, at_ms=3_000, title=title
            )
        assert invalid.value.kind == "invalid"
    assert await store.game_players.count_pending_player_candidates() == 1


async def test_a_wrongly_approved_self_statement_can_be_revoked_or_deleted(store: SQLiteDatabase) -> None:
    """批错了：撤销批准撤下玩家卡、回到待审（期限从撤销时重算）；删掉这一条哪种状态都行；动作都记进改动记录，不带文本。"""
    session = await _session(store)
    repo = store.game_players
    player = await repo.meet_player(
        game="minecraft", uuid="uuid-jia", name="玩家甲", live_session_id=session, at_ms=1_010
    )
    chat = await repo.add_game_chat(
        live_session_id=session,
        game="minecraft",
        direction=GAME_CHAT_PLAYER,
        content="我是服主",
        player_id=player.id,
        at_ms=1_100,
    )
    with store.manager.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO player_candidates(live_session_id, player_id, title, player_chat_id, player_quote,"
            " reply_quote, created_at_ms) VALUES (?, ?, '他是服主', ?, '我是服主', '好的', 2000)",
            (session, player.id, chat),
        )
        candidate_id = int(cur.lastrowid or 0)

    with pytest.raises(MemoryReviewError) as not_approved:
        await repo.revoke_player_candidate(candidate_id=candidate_id, at_ms=2_500)
    assert not_approved.value.kind == "conflict"
    await repo.decide_player_candidate(candidate_id=candidate_id, approve=True, at_ms=3_000)
    assert [item.title for item in await repo.approved_statements(player_id=player.id)] == ["他是服主"]

    revoked = await repo.revoke_player_candidate(candidate_id=candidate_id, at_ms=4_000)
    assert (revoked.status, revoked.decided_at_ms, revoked.created_at_ms) == ("pending", None, 4_000)
    assert await repo.approved_statements(player_id=player.id) == [], "撤下玩家卡"
    assert await repo.expire_player_candidates(now_ms_value=2_001 + 14 * 24 * 60 * 60 * 1000) == 0, "期限从撤销时重算"

    await repo.delete_player_candidate(candidate_id=candidate_id, at_ms=5_000)
    assert await repo.list_player_candidates() == []
    with pytest.raises(MemoryReviewError) as gone:
        await repo.delete_player_candidate(candidate_id=candidate_id, at_ms=6_000)
    assert gone.value.kind == "not_found"
    with store.manager.transaction() as conn:
        edits = [
            tuple(row)
            for row in conn.execute(
                "SELECT target, action, candidate_id, before_text, after_text FROM memory_edits ORDER BY id"
            )
        ]
    assert edits == [
        ("player_candidate", action, candidate_id, "", "")
        for action in ("player_approve", "player_revoke", "player_delete")
    ]
