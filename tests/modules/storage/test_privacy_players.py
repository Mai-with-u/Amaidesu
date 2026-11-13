"""删游戏玩家：级联删除范围、名字换成"一位玩家"、进不记录名单后再出现不记；删观众连带运营者确认过的玩家。"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator, Dict, Tuple

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameChatPayload, GameSelfChattedPayload
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


async def _she_types(bus: EventBus, session_id: int, message: str, at_ms: int) -> None:
    """她在公屏打出去一句。"""
    await bus.emit(
        CoreEvents.GAME_SELF_CHATTED,
        GameSelfChattedPayload(game="minecraft", message=message, live_session_id=session_id, timestamp_ms=at_ms),
        source="test",
    )
    await bus.wait_until_drained()


async def test_her_public_chat_drops_a_deleted_player_everywhere_and_stays_masked_later(
    wired: Tuple[SQLiteDatabase, PrivacyService],
) -> None:
    """删玩家时她所有场次的公屏话都换名字（含他没留记录的场次）；之后的场次他再来，她打的字落库前也遮掉。"""
    store, privacy = wired
    ids = await _alex_scene(store)
    quiet = await store.sessions.insert_live_session(started_at_ms=3_000, kind="live")
    await store.game_players.add_game_chat(
        live_session_id=quiet, game="minecraft", direction="streamer", content="Alex 今天没来呀", at_ms=3_010
    )
    assert await privacy.delete_player(player_id=ids["alex"]) is True
    lines = await store.game_players.list_session_game_chat(live_session_id=quiet)
    assert [line.content for line in lines] == ["一位玩家 今天没来呀"], "他没留记录的场次也换"

    later = await store.sessions.insert_live_session(started_at_ms=9_000, kind="live")
    bus = EventBus()
    ledger = GamePlayerLedger(bus, store.game_players, privacy)
    await ledger.start()
    try:
        await _say(bus, later, "我又回来了", 9_010)
        await _she_types(bus, later, "Alex 你怎么又来了", 9_020)
    finally:
        await ledger.stop()
        await bus.cleanup()
    lines = await store.game_players.list_session_game_chat(live_session_id=later)
    assert [(line.direction, line.content) for line in lines] == [
        ("other", ""),
        ("streamer", "一位玩家 你怎么又来了"),
    ]


async def test_forget_me_as_his_first_line_masks_her_chat_and_can_still_be_retracted(
    wired: Tuple[SQLiteDatabase, PrivacyService],
) -> None:
    """开口第一句就是"忘了我"（还没有玩家行）：她打的字里遮掉他；宽限期内撤回照样恢复记录。"""
    store, privacy = wired
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    bus = EventBus()
    ledger = GamePlayerLedger(bus, store.game_players, privacy)
    await ledger.start()
    try:
        await _say(bus, session, "忘了我", 1_100)
        await _she_types(bus, session, "好的 Alex，不记你", 1_200)
        assert await privacy.is_player_forgotten(game="minecraft", uuid=_ALEX) is True
        await _say(bus, session, "算了", 1_300)
        await _say(bus, session, "还是记着吧", 1_400)
    finally:
        await ledger.stop()
        await bus.cleanup()
    assert await privacy.is_player_forgotten(game="minecraft", uuid=_ALEX) is False
    lines = await store.game_players.list_session_game_chat(live_session_id=session)
    assert [(line.direction, line.content) for line in lines] == [
        ("other", ""),
        ("streamer", "好的 一位玩家，不记你"),
        ("other", ""),
        ("player", "还是记着吧"),
    ]
    assert await store.game_players.find_player(game="minecraft", uuid=_ALEX) is not None


async def test_rowless_forget_me_cannot_be_retracted_after_the_grace_period(
    wired: Tuple[SQLiteDatabase, PrivacyService],
) -> None:
    """没有玩家行时发的"忘了我"，发令后已有场次下播就过了宽限期：撤回短语不再生效，他留在名单里。"""
    store, privacy = wired
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    assert (
        await privacy.process_incoming_player_chat(
            game="minecraft", uuid=_ALEX, name="Alex", content="忘了我", at_ms=1_100
        )
        == "forget_command"
    )
    await store.sessions.close_live_session(live_session_id=session, ended_at_ms=2_000)
    assert (
        await privacy.process_incoming_player_chat(
            game="minecraft", uuid=_ALEX, name="Alex", content="算了", at_ms=3_000
        )
        == "forget_listed"
    )
    assert await privacy.is_player_forgotten(game="minecraft", uuid=_ALEX) is True


async def test_deleting_a_player_masks_his_name_in_other_players_self_statements(
    wired: Tuple[SQLiteDatabase, PrivacyService],
) -> None:
    """玩家甲的自述里提到了玩家乙：删乙时只把名字换成"一位玩家"，甲的候选本身留着；乙自己的候选随他删。"""
    store, privacy = wired
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    repo = store.game_players
    jia = await repo.meet_player(game="minecraft", uuid="uuid-jia", name="玩家甲", live_session_id=session, at_ms=1_010)
    yi = await repo.meet_player(game="minecraft", uuid="uuid-yi", name="玩家乙", live_session_id=session, at_ms=1_020)
    said = await repo.add_game_chat(
        live_session_id=session,
        game="minecraft",
        direction="player",
        content="我和玩家乙一起盖的塔",
        player_id=jia.id,
        at_ms=1_030,
    )
    his = await repo.add_game_chat(
        live_session_id=session, game="minecraft", direction="player", content="我住山顶", player_id=yi.id, at_ms=1_040
    )
    with store.manager.transaction() as conn:
        for player_id, chat_id, title, quote in (
            (jia.id, said, "他和玩家乙一起盖了塔", "和玩家乙一起盖的塔"),
            (yi.id, his, "他的家在山顶", "山顶"),
        ):
            conn.execute(
                "INSERT INTO player_candidates(live_session_id, player_id, title, player_chat_id, player_quote,"
                " reply_quote, created_at_ms) VALUES (?, ?, ?, ?, ?, '真厉害', 1050)",
                (session, player_id, title, chat_id, quote),
            )

    assert await privacy.delete_player(player_id=yi.id) is True
    with store.manager.transaction() as conn:
        rows = [tuple(row) for row in conn.execute("SELECT player_id, title, player_quote FROM player_candidates")]
    assert rows == [(jia.id, "他和一位玩家一起盖了塔", "和一位玩家一起盖的塔")]


async def test_deleting_player_and_viewer_scrubs_social_facts_work_docs_and_checkpoints(
    wired: Tuple[SQLiteDatabase, PrivacyService],
) -> None:
    """删玩家与删观众：本人话语作证的社交事实整条删掉不留原话；他人作证行、跨重启工作文档与更正/断点同步清理。"""
    store, privacy = wired
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")

    # 玩家乙曾用名更长，核对从长到短替换不留残截
    jia = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-p-jia", name="玩家甲", live_session_id=session, at_ms=1_010
    )
    await store.game_players.meet_player(
        game="minecraft", uuid="uuid-p-yi", name="玩家乙的长曾用名", live_session_id=session, at_ms=1_015
    )
    yi = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-p-yi", name="玩家乙", live_session_id=session, at_ms=1_020
    )
    jia_chat = await store.game_players.add_game_chat(
        live_session_id=session,
        game="minecraft",
        direction="player",
        content="公共仓库是我和玩家乙共用的",
        player_id=jia.id,
        save_id="w1",
        save_name="存档一",
        at_ms=1_030,
        message_id="gc-jia",
    )
    yi_chat = await store.game_players.add_game_chat(
        live_session_id=session,
        game="minecraft",
        direction="player",
        content="我的私房矿洞在东山脚下",
        player_id=yi.id,
        save_id="w1",
        save_name="存档一",
        at_ms=1_040,
        message_id="gc-yi",
    )
    fact_by_yi = await store.social_facts.insert_fact(
        kind="works",
        text="玩家乙在东山挖私房矿洞",
        quote="我的私房矿洞在东山脚下",
        game="minecraft",
        save_id="w1",
        evidence_game_chat_id=yi_chat,
        created_session_id=session,
        updated_session_id=session,
    )
    fact_by_jia = await store.social_facts.insert_fact(
        kind="sharing",
        text="公共仓库由玩家甲与玩家乙共用",
        quote="我和玩家乙共用的",
        game="minecraft",
        save_id="w1",
        evidence_game_chat_id=jia_chat,
        created_session_id=session,
        updated_session_id=session,
    )
    assert fact_by_yi is not None and fact_by_jia is not None

    # 观众乙发过弹幕，假设存量库里有一条挂在该弹幕上的社交事实，且游戏工作文档与断点中也出现了名字
    viewer_res = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="uid-viewer-b",
        user_name="观众乙",
        timestamp_ms=1_050,
        name_match_window_ms=0,
    )
    v_chat = await store.chat.insert_live_chat(
        live_session_id=session,
        timestamp_ms=1_055,
        sender_role="viewer",
        sender_name="观众乙",
        sender_id="uid-viewer-b",
        viewer_id=viewer_res.viewer_id,
        content="大家说好西边牧场是公用的",
        message_type="danmaku",
        message_id="dm-vb",
    )
    fact_by_viewer = await store.social_facts.insert_fact(
        kind="sharing",
        text="西边牧场公用",
        quote="西边牧场是公用的",
        game="minecraft",
        save_id="w1",
        evidence_chat_id=v_chat,
        created_session_id=session,
        updated_session_id=session,
    )
    assert fact_by_viewer is not None

    await store.game_work_docs.save_work_docs(
        game="minecraft",
        save_id="w1",
        save_name="存档一",
        notebook="帮玩家乙的长曾用名（现名玩家乙）和观众乙修栅栏",
        todos=[
            {"content": "给玩家乙送木板", "status": "in_progress"},
            {"content": "替观众乙立路牌", "status": "pending"},
        ],
        updated_session_id=session,
    )
    await store.memory_corrections.insert_and_apply(
        live_session_id=session,
        target_kind="general",
        verdict="invalid",
        summary="玩家乙说矿洞封了",
        evidence_kind="player_chat",
        evidence_quote="我的私房矿洞在东山脚下",
        evidence_game_chat_id=yi_chat,
    )
    await store.memory_corrections.insert_and_apply(
        live_session_id=session,
        target_kind="general",
        verdict="note",
        summary="运营提醒：玩家乙和观众乙刚换了基地",
        evidence_kind="operator",
        evidence_quote="玩家乙和观众乙刚换了基地",
        source="operator",
    )
    await store.sessions.save_settle_checkpoint(
        live_session_id=session,
        step="archive",
        fingerprint="fp1",
        output_json='{"archive":[{"title":"和玩家乙挖矿"}]}',
        saved_at_ms=1_100,
    )

    # 1. 删玩家乙：他本人公屏作证的社交事实与更正整行删除，甲作证行与运营更正换成"一位玩家"，工作文档与断点同步清理
    assert await privacy.delete_player(player_id=yi.id) is True
    assert await store.social_facts.get_fact(fact_id=fact_by_yi) is None
    kept_jia = await store.social_facts.get_fact(fact_id=fact_by_jia)
    assert kept_jia is not None
    assert kept_jia.text == "公共仓库由玩家甲与一位玩家共用"
    assert kept_jia.quote == "我和一位玩家共用的"

    doc_after_player = await store.game_work_docs.get_work_docs(game="minecraft", save_id="w1")
    assert doc_after_player is not None
    assert "玩家乙" not in doc_after_player.notebook
    assert doc_after_player.notebook == "帮一位玩家（现名一位玩家）和观众乙修栅栏"
    assert doc_after_player.todos[0] == {"content": "给一位玩家送木板", "status": "in_progress"}

    corrs = await store.memory_corrections.list_by_session(live_session_id=session)
    assert len(corrs) == 1
    assert corrs[0].summary == "运营提醒：一位玩家和观众乙刚换了基地"
    assert await store.sessions.get_settle_checkpoint(live_session_id=session, step="archive") is None

    # 2. 删观众乙：挂在其弹幕上的社交事实在外键置空前整行删除，工作文档与运营更正里的观众昵称换成"一位观众"
    assert await privacy.delete_viewer(viewer_id=viewer_res.viewer_id, add_to_forget_list=True) is True
    assert await store.social_facts.get_fact(fact_id=fact_by_viewer) is None
    doc_after_viewer = await store.game_work_docs.get_work_docs(game="minecraft", save_id="w1")
    assert doc_after_viewer is not None
    assert "观众乙" not in doc_after_viewer.notebook
    assert doc_after_viewer.notebook == "帮一位玩家（现名一位玩家）和一位观众修栅栏"
    assert doc_after_viewer.todos[1] == {"content": "替一位观众立路牌", "status": "pending"}
    corrs_after_viewer = await store.memory_corrections.list_by_session(live_session_id=session)
    assert corrs_after_viewer[0].summary == "运营提醒：一位玩家和一位观众刚换了基地"
