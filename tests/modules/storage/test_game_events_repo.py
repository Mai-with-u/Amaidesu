"""game_events 按场次查玩过哪些游戏：长期笔记淡出时只跟某个游戏有关的条目只数玩过它的场次"""

from __future__ import annotations

from src.modules.storage.database import SQLiteDatabase


async def _session(store: SQLiteDatabase, started: int) -> int:
    return await store.sessions.insert_live_session(stream_id="room", platform="bilibili", started_at_ms=started)


async def test_games_by_session_dedupes_and_skips_sessions_without_events(store: SQLiteDatabase) -> None:
    played, chatted, other = await _session(store, 1_000), await _session(store, 2_000), await _session(store, 3_000)
    for game, ts in (("minecraft", 1_100), ("minecraft", 1_200), ("warudo_demo", 1_300), ("minecraft", 3_100)):
        sid = played if ts < 2_000 else other
        await store.events.insert_game_event(
            live_session_id=sid, game=game, event_type="milestone", message="m", timestamp_ms=ts
        )

    games = await store.events.list_games_by_session(live_session_ids=[played, chatted])

    # 只查问到的场次；杂谈场没有游戏事件，不出现在结果里
    assert games == {played: {"minecraft", "warudo_demo"}}
    assert await store.events.list_games_by_session(live_session_ids=[]) == {}
