"""玩家卡：游戏里的人开口或冲她做动作时，推一张"这位是谁、和你有过什么"。

覆盖：
- 只推给这一窗找过她的那位（按 UUID 对上），卡面写往来事实与他当时的名字（改过名写以前叫什么）
- 新面孔、只在本场或彩排里有往来的不推
- 同一位每场至多两次、隔十分钟；本场她已经回应过他就不再推（用上即停）
- 推出去的卡记进线索卡记录，只记玩家编号
- 答应过他的事写在他的卡尾；对玩家的承诺不进常驻简报、不当"游戏开着"的卡推给全场
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator, Tuple

import pytest

from src.agents.streamer.memory.brief import build_brief
from src.agents.streamer.memory.cues import PUSH_COOLDOWN_MS, MemoryCueProvider
from src.agents.streamer.memory.render import render_player_card
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.game_players import GamePlayer

_NOW = 1_000_000_000_000


class _Clock:
    """可拨动的时钟：玩家卡的冷却按它算。"""

    def __init__(self, value: int = _NOW) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


@pytest.fixture()
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    db = SQLiteDatabase(tmp_path / "players.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


async def _two_sessions(store: SQLiteDatabase) -> Tuple[int, int]:
    """上一场（正式场，已结束）与这一场。"""
    before = await store.sessions.insert_live_session(started_at_ms=_NOW - 86_400_000, kind="live")
    await store.sessions.close_live_session(live_session_id=before, ended_at_ms=_NOW - 80_000_000)
    current = await store.sessions.insert_live_session(started_at_ms=_NOW, kind="live")
    return before, current


def _provider(store: SQLiteDatabase, current: int, clock: _Clock, previous: int) -> MemoryCueProvider:
    async def previous_session() -> int:
        return previous

    return MemoryCueProvider(
        commitments_repo=store.commitments,
        chat_repo=store.chat,
        viewer_repo=store.viewers,
        current_session_provider=lambda: current,
        previous_session_provider=previous_session,
        clock=clock,
        archive_repo=store.archive,
        cue_log_repo=store.memory_cue_log,
        passive_enabled=False,
        game_player_repo=store.game_players,
    )


async def _old_friend(store: SQLiteDatabase, before: int, current: int) -> int:
    """Alex（以前叫 Steve）：上一场打过招呼、送过面包、切磋赢了他一局；这一场又来了。"""
    repo = store.game_players
    alex = await repo.meet_player(
        game="minecraft", uuid="uuid-a", name="Steve", live_session_id=before, at_ms=_NOW - 85_000_000
    )
    await repo.record_greeting(player_id=alex.id, live_session_id=before, actions=["蹲起"], at_ms=_NOW - 85_000_000)
    await repo.record_greeting(player_id=alex.id, live_session_id=before, actions=["挥手"], at_ms=_NOW - 84_000_000)
    await repo.add_interaction(
        player_id=alex.id,
        live_session_id=before,
        kind="gift",
        fact={"said": "朝我扔了 3 个面包"},
        at_ms=_NOW - 83_000_000,
    )
    await repo.add_interaction(
        player_id=alex.id, live_session_id=before, kind="spar", fact={"result": "won"}, at_ms=_NOW - 82_000_000
    )
    await repo.meet_player(game="minecraft", uuid="uuid-a", name="Alex", live_session_id=current, at_ms=_NOW)
    return alex.id


async def test_an_old_friend_gets_a_card_with_what_happened_between_them(store: SQLiteDatabase) -> None:
    before, current = await _two_sessions(store)
    alex_id = await _old_friend(store, before, current)
    clock = _Clock()
    cues = await _provider(store, current, clock, before).collect(batch=[], game_players=[("minecraft", "uuid-a")])

    assert cues.card_count == 1
    assert "[玩家·Alex] Alex（以前叫 Steve） 在游戏里找你了。" in cues.text
    assert "上一场他冲你打过 2 次招呼、送过你东西（朝我扔了 3 个面包）、和你切磋过 1 场（你赢 1 局）" in cues.text
    (entry,) = await store.memory_cue_log.list_session_entries(live_session_id=current)
    assert (entry.card_kind, entry.sources, entry.detail) == ("player", ["player"], {"player_id": alex_id})


async def test_no_card_for_new_faces_or_players_who_did_not_speak(store: SQLiteDatabase) -> None:
    before, current = await _two_sessions(store)
    await _old_friend(store, before, current)
    # 新面孔：只在本场见过，没有以前的往来
    newbie = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-n", name="Newbie", live_session_id=current, at_ms=_NOW
    )
    await store.game_players.record_greeting(player_id=newbie.id, live_session_id=current, actions=["挥手"], at_ms=_NOW)
    provider = _provider(store, current, _Clock(), before)

    cues = await provider.collect(batch=[], game_players=[("minecraft", "uuid-n"), ("minecraft", "uuid-unknown")])
    assert cues.card_count == 0
    # 老朋友这一窗没开口：不推他的卡
    assert (await provider.collect(batch=[], game_players=[])).card_count == 0


async def test_rehearsal_history_does_not_count(store: SQLiteDatabase) -> None:
    rehearsal = await store.sessions.insert_live_session(started_at_ms=_NOW - 86_400_000, kind="rehearsal")
    current = await store.sessions.insert_live_session(started_at_ms=_NOW, kind="live")
    tester = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-t", name="Tester", live_session_id=rehearsal, at_ms=_NOW - 85_000_000
    )
    await store.game_players.record_greeting(
        player_id=tester.id, live_session_id=rehearsal, actions=["蹲起"], at_ms=_NOW - 85_000_000
    )
    cues = await _provider(store, current, _Clock(), rehearsal).collect(
        batch=[], game_players=[("minecraft", "uuid-t")]
    )
    assert cues.card_count == 0


async def test_cooldown_cap_and_stop_once_she_answered_him(store: SQLiteDatabase) -> None:
    before, current = await _two_sessions(store)
    alex_id = await _old_friend(store, before, current)
    clock = _Clock()
    provider = _provider(store, current, clock, before)
    window = [("minecraft", "uuid-a")]

    assert (await provider.collect(batch=[], game_players=window)).card_count == 1
    # 十分钟内他再开口：不再推
    clock.value += PUSH_COOLDOWN_MS - 1
    assert (await provider.collect(batch=[], game_players=window)).card_count == 0
    clock.value += 1
    assert (await provider.collect(batch=[], game_players=window)).card_count == 1
    # 每场至多两次
    clock.value += PUSH_COOLDOWN_MS
    assert (await provider.collect(batch=[], game_players=window)).card_count == 0

    # 另一场：她这一场已经回应过他，就不推了（用上即停）
    later = await store.sessions.insert_live_session(started_at_ms=_NOW + 86_400_000, kind="live")
    await store.chat.insert_live_chat(
        live_session_id=later,
        timestamp_ms=_NOW + 86_400_100,
        sender_role="assistant",
        content="又是你呀 Alex",
        message_type="speak",
        reply_player_id=alex_id,
    )
    answered = _provider(store, later, clock, current)
    assert (await answered.collect(batch=[], game_players=window)).card_count == 0


async def test_promises_to_him_ride_on_his_card_only(store: SQLiteDatabase) -> None:
    """答应过 Alex 的事：他露面时写在他的卡尾（没有别的往来也推），简报和游戏开着的卡都不提。"""
    before, current = await _two_sessions(store)
    alex = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-a", name="Alex", live_session_id=before, at_ms=_NOW - 85_000_000
    )
    promise = await store.commitments.insert_commitment(
        what="帮他修房子", quote="周末我帮你把房子修好", created_session_id=before, now_ms_value=_NOW - 84_000_000
    )
    assert promise is not None
    with store.manager.transaction() as conn:
        conn.execute("UPDATE commitments SET target_player_id=?, game='minecraft' WHERE id=?", (alex.id, promise))

    brief = await build_brief(archive_repo=store.archive, commitments_repo=store.commitments)
    assert "帮他修房子" not in brief.text and promise not in brief.commitment_ids
    provider = _provider(store, current, _Clock(), before)
    assert (await provider.collect(batch=[], open_games=["minecraft"])).card_count == 0, "游戏开着也不对全场推"

    cues = await provider.collect(batch=[], open_games=["minecraft"], game_players=[("minecraft", "uuid-a")])
    assert cues.card_count == 1
    assert f"[玩家·Alex] Alex 在游戏里找你了。你答应过他：帮他修房子（约#{promise}）。" in cues.text
    (entry,) = await store.memory_cue_log.list_session_entries(live_session_id=current)
    assert entry.detail == {"player_id": alex.id, "commitment_ids": [promise]}
    # 卡尾印着的约#N 是本批能点名的编号，说话档按他的名字说"答应过谁"
    assert (cues.commitment_ids, cues.labels) == ((promise,), {promise: "Alex"})


async def test_a_promise_she_already_used_drops_off_his_card(store: SQLiteDatabase) -> None:
    """本场已经点名用上的那件事不再写在他的卡上（用上即停）；卡上没别的往来就不推。"""
    before, current = await _two_sessions(store)
    alex = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-a", name="Alex", live_session_id=before, at_ms=_NOW - 85_000_000
    )
    promise = await store.commitments.insert_commitment(
        what="帮他修房子", quote="周末我帮你把房子修好", created_session_id=before, now_ms_value=_NOW - 84_000_000
    )
    assert promise is not None
    with store.manager.transaction() as conn:
        conn.execute("UPDATE commitments SET target_player_id=? WHERE id=?", (alex.id, promise))
    await store.chat.insert_live_chat(
        live_session_id=current,
        timestamp_ms=_NOW + 1_000,
        sender_role="assistant",
        content="房子的事我记着呢",
        message_type="speech",
        memory_refs=f'["约#{promise}"]',
    )

    cues = await _provider(store, current, _Clock(), before).collect(batch=[], game_players=[("minecraft", "uuid-a")])

    assert (cues.card_count, cues.commitment_ids) == (0, ())


async def test_no_card_for_a_player_who_asked_to_be_forgotten(store: SQLiteDatabase) -> None:
    """他在公屏发过"忘了我"、还在宽限期里：不再提起他。"""
    before, current = await _two_sessions(store)
    alex_id = await _old_friend(store, before, current)
    with store.manager.transaction() as conn:
        conn.execute("UPDATE game_players SET forget_requested_at_ms=? WHERE id=?", (_NOW, alex_id))
    cues = await _provider(store, current, _Clock(), before).collect(batch=[], game_players=[("minecraft", "uuid-a")])
    assert cues.card_count == 0


async def test_approved_self_statements_ride_on_his_card_only(store: SQLiteDatabase) -> None:
    """他说过、人批准过的自述写在他的卡上（没有别的往来也推）；待审的不写，简报不提。"""
    before, current = await _two_sessions(store)
    repo = store.game_players
    alex = await repo.meet_player(
        game="minecraft", uuid="uuid-a", name="Alex", live_session_id=before, at_ms=_NOW - 85_000_000
    )
    ids = []
    for content, title in (("我在雪山脚下挖了基地", "他的基地在雪山脚下"), ("我的猫叫团子", "他的猫叫团子")):
        chat = await repo.add_game_chat(
            live_session_id=before,
            game="minecraft",
            direction="player",
            content=content,
            player_id=alex.id,
            save_id="w1",
            save_name="生存一档",
            at_ms=_NOW - 84_000_000,
        )
        with store.manager.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO player_candidates(live_session_id, player_id, title, player_chat_id, player_quote,"
                " reply_quote, created_at_ms) VALUES (?, ?, ?, ?, ?, '真好', ?)",
                (before, alex.id, title, chat, content, _NOW - 80_000_000),
            )
            ids.append(int(cur.lastrowid or 0))
    await repo.decide_player_candidate(candidate_id=ids[0], approve=True, at_ms=_NOW - 70_000_000)

    brief = await build_brief(archive_repo=store.archive, commitments_repo=store.commitments)
    assert "雪山脚下" not in brief.text, "自述不进常驻简报"
    cues = await _provider(store, current, _Clock(), before).collect(batch=[], game_players=[("minecraft", "uuid-a")])
    assert cues.card_count == 1
    # 这一场没报存档（不分存档）时不加"在哪个存档"的注；分存档时的注与答应过他的事同一个说法
    assert "[玩家·Alex] Alex 在游戏里找你了。他跟你说过：他的基地在雪山脚下。认得出他就好" in cues.text
    assert "团子" not in cues.text, "没批准的不写"
    (entry,) = await store.memory_cue_log.list_session_entries(live_session_id=current)
    assert entry.detail == {"player_id": alex.id, "statement_ids": [ids[0]]}


def test_self_statements_from_another_save_say_where() -> None:
    """别的存档里说的照样提，但写明在哪个存档：那座房子不一定在眼下这个世界里。"""
    player = GamePlayer(
        id=1, game="minecraft", uuid="uuid-a", name="Alex", names=["Alex"], first_seen_ms=0, last_seen_ms=0
    )
    text = render_player_card(
        player, [], statements=[("他的基地在雪山脚下", "在 minecraft「生存一档」里，不是眼下这个存档")]
    )
    assert "他跟你说过：他的基地在雪山脚下（在 minecraft「生存一档」里，不是眼下这个存档）。" in text
