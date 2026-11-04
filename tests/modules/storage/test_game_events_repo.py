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


async def test_save_spans_follow_each_game_in_time_order(store: SQLiteDatabase) -> None:
    """同一游戏 A → B → 回 A 是三段；不报存档的事件不参与；改名后段名取最后报的；别的场不混进来。"""
    sid, other = await _session(store, 1_000), await _session(store, 9_000)
    rows = (
        ("minecraft", 1_100, "a", "生存档"),
        ("minecraft", 1_200, "", ""),
        ("minecraft", 1_300, "a", "生存一档"),
        ("text_adv", 1_350, "", ""),
        ("minecraft", 1_400, "b", "新档"),
        ("minecraft", 1_500, "a", "生存一档"),
    )
    for game, ts, save_id, save_name in rows:
        await store.events.insert_game_event(
            live_session_id=sid,
            game=game,
            event_type="milestone",
            message="m",
            timestamp_ms=ts,
            save_id=save_id,
            save_name=save_name,
        )
    await store.events.insert_game_event(
        live_session_id=other, game="minecraft", event_type="milestone", message="m", timestamp_ms=9_100, save_id="c"
    )

    spans = await store.events.list_session_save_spans(live_session_id=sid)

    assert [(span.game, span.save_id, span.save_name, span.started_ms, span.ended_ms) for span in spans] == [
        ("minecraft", "a", "生存一档", 1_100, 1_300),
        ("minecraft", "b", "新档", 1_400, 1_400),
        ("minecraft", "a", "生存一档", 1_500, 1_500),
    ]
