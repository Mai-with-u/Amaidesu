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


async def test_startup_settles_every_open_delegation_as_interrupted_once(store: SQLiteDatabase) -> None:
    """启动收尾：库里截止时刻前受理、还没了结的委派（不论哪一场、谁派的）各记一条被打断的终态，记在它受理的
    那一场；已了结的不动；截止时刻之后受理的不算；再收尾一次不重复记。"""
    old, sid = await _session(store, 500), await _session(store, 1_000)
    await _delegation(store, old, "deleg_0", "accepted", "上一场没做完的", 600, initiator="operator")
    await _delegation(store, sid, "deleg_1", "accepted", "把门装好", 1_100)
    await _delegation(store, sid, "deleg_1", "cancelled", "被取消（source=operator）", 1_200)
    await _delegation(store, sid, "deleg_2", "accepted", "去挖铁矿", 1_300)
    await _delegation(store, sid, "deleg_3", "accepted", "启动后刚派的", 4_500)

    settled = await store.events.settle_interrupted_delegations(
        text="重启前在做，被打断了，做到哪不确定", timestamp_ms=5_000, accepted_before_ms=4_000
    )

    assert [(r.task_id, r.live_session_id, r.initiator, r.status, r.ended_ms) for r in settled] == [
        ("deleg_0", old, "operator", "interrupted", 5_000),
        ("deleg_2", sid, "streamer", "interrupted", 5_000),
    ]
    again = await store.events.settle_interrupted_delegations(text="x", timestamp_ms=6_000, accepted_before_ms=4_000)
    assert again == []
    statuses = {r.task_id: r.status for r in await store.events.list_session_delegations(live_session_id=sid)}
    assert statuses == {"deleg_1": "cancelled", "deleg_2": "interrupted", "deleg_3": "accepted"}
    [old_record] = await store.events.list_session_delegations(live_session_id=old)
    assert (old_record.status, old_record.summary) == ("interrupted", "重启前在做，被打断了，做到哪不确定")


async def test_list_session_game_events_sampled_covers_full_span_and_prioritizes_high_signal(
    store: SQLiteDatabase,
) -> None:
    """长场跨全时段分桶抽样：保留前中后段主线与高信号事件，同时不破坏 list_session_game_events 原契约。"""
    sid = await _session(store, 1_000_000)
    minute = 60_000
    # 前半场（10–40 分钟）：只有稀疏但极其关键的跨维度与阵亡事件
    await store.events.insert_game_event(
        live_session_id=sid,
        game="minecraft",
        event_type="dimension_changed",
        message="从主世界到了下界",
        timestamp_ms=1_000_000 + 10 * minute,
    )
    await store.events.insert_game_event(
        live_session_id=sid,
        game="minecraft",
        event_type="died",
        message="在下界堡垒阵亡",
        timestamp_ms=1_000_000 + 25 * minute,
    )
    # 中段（80 分钟）：阶段汇报
    await store.events.insert_game_event(
        live_session_id=sid,
        game="minecraft",
        event_type="report",
        message="交付：建成动力磨坊",
        timestamp_ms=1_000_000 + 80 * minute,
    )
    # 后半场（100–170 分钟）：连续 140 条密集常规事件
    for idx in range(140):
        await store.events.insert_game_event(
            live_session_id=sid,
            game="minecraft",
            event_type="attention_required" if idx % 3 else "milestone",
            message=f"后半场事件 #{idx}",
            timestamp_ms=1_000_000 + 100 * minute + idx * 30_000,
        )
    # 地点动静与委派账仍须排除
    await store.events.insert_place_mark(
        live_session_id=sid, game="minecraft", kind="near", place="基地", timestamp_ms=1_000_000 + 5 * minute
    )
    await _delegation(store, sid, "deleg_x", "accepted", "造风车", 1_000_000 + 15 * minute)

    # 既有读口仍保持末尾 limit 条契约（不含前半场）
    tail_only = await store.events.list_session_game_events(live_session_id=sid, limit=60)
    assert len(tail_only) == 60
    assert all(ev.event_type != "dimension_changed" for ev in tail_only)

    # 新分桶读口覆盖前中后全时段、严格时间正序且优先保留高信号事件
    sampled = await store.events.list_session_game_events_sampled(live_session_id=sid, limit=60)
    assert len(sampled) == 60
    assert [ev.timestamp_ms for ev in sampled] == sorted(ev.timestamp_ms for ev in sampled)
    messages = [ev.message for ev in sampled]
    assert "从主世界到了下界" in messages
    assert "在下界堡垒阵亡" in messages
    assert "交付：建成动力磨坊" in messages
    assert any(msg.startswith("后半场事件 #") for msg in messages)
    assert all(not ev.event_type.startswith(("place_", "delegation_")) for ev in sampled)


async def test_list_session_game_events_filters_by_event_types_and_samples_task_succeeded(
    store: SQLiteDatabase,
) -> None:
    """event_types 白名单过滤让高频 attention_required 不挤掉前半场 task_succeeded；分桶采样优先保单步成功与交付。"""
    sid = await _session(store, 1_000_000)
    await store.events.insert_game_event(
        live_session_id=sid,
        game="minecraft",
        event_type="task_succeeded",
        message="对动力压机使用成功，压出 4 块铁板",
        timestamp_ms=1_001_000,
    )
    await store.events.insert_game_event(
        live_session_id=sid,
        game="minecraft",
        event_type="item_delivery",
        message="朝同伴扔出 2 个木板",
        timestamp_ms=1_002_000,
    )
    for idx in range(70):
        await store.events.insert_game_event(
            live_session_id=sid,
            game="minecraft",
            event_type="attention_required",
            message=f"常规告警 #{idx}",
            timestamp_ms=1_010_000 + idx * 1_000,
        )

    # 未过滤时最近 60 条全被 attention_required 占满
    tail_all = await store.events.list_session_game_events(live_session_id=sid, limit=60)
    assert all(ev.event_type == "attention_required" for ev in tail_all)

    # 按作证白名单过滤时，前半场 task_succeeded 与 item_delivery 完整保留
    proof_only = await store.events.list_session_game_events(
        live_session_id=sid,
        limit=60,
        event_types=("milestone", "report", "dimension_changed", "item_delivery", "task_succeeded"),
    )
    assert [ev.event_type for ev in proof_only] == ["task_succeeded", "item_delivery"]

    # 跨时段采样同样优先保留高信号 task_succeeded 与 item_delivery
    sampled = await store.events.list_session_game_events_sampled(live_session_id=sid, limit=20)
    sampled_types = {ev.event_type for ev in sampled}
    assert {"task_succeeded", "item_delivery"} <= sampled_types
