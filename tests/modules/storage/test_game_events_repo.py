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


async def _delegation(
    store: SQLiteDatabase, sid: int, task_id: str, kind: str, text: str, ts: int, initiator: str = "streamer"
) -> None:
    await store.events.insert_delegation_event(
        live_session_id=sid,
        game="minecraft",
        task_id=task_id,
        kind=kind,
        text=text,
        timestamp_ms=ts,
        initiator=initiator,
    )


async def test_session_delegations_pair_rows_across_sessions_and_keep_open_ones_open(store: SQLiteDatabase) -> None:
    """开播前受理、开播后了结的委派：受理行在别的场次也配得上；还没了结的记 accepted、了结时刻为 0。"""
    before, live = await _session(store, 1_000), await _session(store, 2_000)
    await _delegation(store, before, "deleg_1", "accepted", "搭个棚子", 1_500)
    await _delegation(store, live, "deleg_1", "succeeded", "delivery: 棚子搭好了", 2_100)
    await _delegation(store, live, "deleg_2", "accepted", "去砍树", 2_200, initiator="operator")

    records = await store.events.list_session_delegations(live_session_id=live)

    assert [
        (r.task_id, r.live_session_id, r.initiator, r.instruction, r.status, r.started_ms, r.ended_ms) for r in records
    ] == [
        ("deleg_1", before, "streamer", "搭个棚子", "succeeded", 1_500, 2_100),
        ("deleg_2", live, "operator", "去砍树", "accepted", 2_200, 0),
    ]


async def test_resume_settles_open_delegations_as_interrupted_once(store: SQLiteDatabase) -> None:
    """续播认领：重启前受理、还没了结的各记一条被打断的终态并返回；已了结的不动；
    本进程启动后才受理的不算被打断；再认领一次不重复记。"""
    sid = await _session(store, 1_000)
    await _delegation(store, sid, "deleg_1", "accepted", "把门装好", 1_100)
    await _delegation(store, sid, "deleg_1", "cancelled", "被取消（source=operator）", 1_200)
    await _delegation(store, sid, "deleg_2", "accepted", "去挖铁矿", 1_300)
    await _delegation(store, sid, "deleg_3", "accepted", "重启后刚派的", 4_500)

    settled = await store.events.settle_interrupted_delegations(
        live_session_id=sid, text="重启前在做，被打断了，做到哪不确定", timestamp_ms=5_000, accepted_before_ms=4_000
    )

    assert [(r.task_id, r.instruction, r.status, r.ended_ms) for r in settled] == [
        ("deleg_2", "去挖铁矿", "interrupted", 5_000)
    ]
    again = await store.events.settle_interrupted_delegations(
        live_session_id=sid, text="x", timestamp_ms=6_000, accepted_before_ms=4_000
    )
    assert again == []
    statuses = {r.task_id: r.status for r in await store.events.list_session_delegations(live_session_id=sid)}
    assert statuses == {"deleg_1": "cancelled", "deleg_2": "interrupted", "deleg_3": "accepted"}
