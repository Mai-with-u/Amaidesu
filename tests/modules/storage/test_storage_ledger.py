"""StorageLedger 集成测试：事件分发落库、观众统计写穿、场次归属与失败隔离"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.interceptors.session_stamp import SessionStampInterceptor
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.game_place import GamePlacePayload
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.session import LiveSessionManager
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.storage_ledger import StorageLedger
from tests.modules.session.fakes import FakeSessionManager
from tests.modules.storage.helpers import make_room_message

# 场次管理器替身解析出的场次主键（模拟"显式场次进行中"）
_FAKE_PK = 4242
_TABLES = ("live_chat", "gifts", "super_chats", "game_events")


@pytest.fixture
async def make_ledger(event_bus: EventBus, store: SQLiteDatabase):
    created: list[StorageLedger] = []

    def _make(pk: Optional[int] = _FAKE_PK, name_match_window_ms: Optional[int] = None) -> StorageLedger:
        ledger = StorageLedger(
            event_bus=event_bus,
            chat_repo=store.chat,
            viewer_repo=store.viewers,
            event_repo=store.events,
            session_manager=FakeSessionManager(active_pk=pk),
            name_match_window_ms=name_match_window_ms,
        )
        created.append(ledger)
        return ledger

    yield _make
    for ledger in created:
        await ledger.stop()


@pytest.fixture
async def ledger(make_ledger) -> StorageLedger:
    ledger = make_ledger()
    await ledger.start()
    return ledger


async def _emit(bus: EventBus, event: str, payload: Any) -> None:
    await bus.emit(event, payload, source="test")
    await bus.wait_until_drained()


def _speech(text: str, **kwargs: Any) -> StreamerSpeechPayload:
    kwargs.setdefault("timestamp_ms", 1_700_000_000_000)
    return StreamerSpeechPayload(
        utterance_id=f"utt_{text}", emotion="neutral", emotion_intensity=0.5, text=text, **kwargs
    )


def _game(event_type: str, message: str = "游戏事件", **kwargs: Any) -> GamePayload:
    return GamePayload(live_session_id=0, game="minecraft", event_type=event_type, message=message, **kwargs)


async def _counts(store: SQLiteDatabase) -> dict[str, int]:
    return {t: (await store.execute(f"SELECT COUNT(*) AS n FROM {t}"))[0]["n"] for t in _TABLES}


@pytest.mark.parametrize(
    ("event", "payload_kwargs", "table", "expected"),
    [
        (
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            {"message_type": "danmaku", "content": "主播好可爱"},
            "live_chat",
            {"content": "主播好可爱", "sender_id": "tester", "message_type": "danmaku", "simulated": 0},
        ),
        (
            CoreEvents.ROOM_MESSAGE_GIFT,
            {"message_type": "gift", "gift_name": "小星星", "gift_count": 5},
            "gifts",
            {"gift_name": "小星星", "quantity": 5, "unit_price": 1000, "total_price": 5000},
        ),
        (
            CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
            {"message_type": "super_chat", "content": "SC 文本", "sc_amount": 99.0},
            "super_chats",
            {"total_price": 99_000, "currency": "bilibili_gold_coin", "message": "SC 文本"},
        ),
        (
            CoreEvents.ROOM_MESSAGE_GUARD,
            {"message_type": "guard", "content": "测试观众 开通了舰长"},
            "live_chat",
            {"content": "测试观众 开通了舰长", "sender_role": "viewer", "message_type": "guard"},
        ),
        (
            CoreEvents.ROOM_MESSAGE_PARTNER_SPEECH,
            {"message_type": "partner_speech", "content": "我来了！"},
            "live_chat",
            {"content": "我来了！", "sender_role": "partner", "message_type": "partner_speech"},
        ),
    ],
)
async def test_room_message_lands_only_in_its_table(
    ledger, store: SQLiteDatabase, event_bus: EventBus, event, payload_kwargs, table, expected
) -> None:
    await _emit(event_bus, event, make_room_message(**payload_kwargs))

    assert await _counts(store) == {t: int(t == table) for t in _TABLES}
    row = (await store.execute(f"SELECT * FROM {table}"))[0]
    assert {k: row[k] for k in expected} == expected
    # 未盖章（0）的载荷经场次管理器解析归属
    assert row["live_session_id"] == _FAKE_PK
    if payload_kwargs["message_type"] == "partner_speech":
        assert await store.execute("SELECT * FROM viewers") == [], "联动对象发言不计观众统计"


async def test_simulated_flag_flows_to_column(ledger, store: SQLiteDatabase, event_bus: EventBus) -> None:
    for content, simulated in (("真弹幕", False), ("假弹幕", True)):
        msg = make_room_message(content=content, simulated=simulated)
        await _emit(event_bus, CoreEvents.ROOM_MESSAGE_DANMAKU, msg)

    rows = await store.execute("SELECT content, simulated FROM live_chat ORDER BY id")
    assert [(r["content"], r["simulated"]) for r in rows] == [("真弹幕", 0), ("假弹幕", 1)]
    real = await store.execute("SELECT content FROM live_chat WHERE simulated=0")
    assert [r["content"] for r in real] == ["真弹幕"]


@pytest.mark.parametrize(
    ("event", "payload_kwargs", "expected"),
    [
        (
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            {"message_type": "danmaku", "content": "第一条弹幕"},
            {"message_count": 1, "gift_count": 0, "replied_count": 0, "interaction_count": 1},
        ),
        (
            CoreEvents.ROOM_MESSAGE_GIFT,
            {"message_type": "gift", "gift_count": 3},
            {"message_count": 0, "gift_count": 1, "interaction_count": 1, "paid_count": 1, "paid_amount": 3000},
        ),
        # SC 与礼物同为付费行为，但不落 live_chat，不计发言
        (
            CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
            {"message_type": "super_chat", "sc_amount": 99.0},
            {"message_count": 0, "interaction_count": 1, "paid_count": 1, "paid_amount": 99_000},
        ),
    ],
)
async def test_room_message_upserts_viewer_stats(
    ledger, store: SQLiteDatabase, event_bus: EventBus, event, payload_kwargs, expected
) -> None:
    user = RoomMessageUser(id="v1", name="观众甲")
    await _emit(event_bus, event, make_room_message(user=user, timestamp_ms=1_700_000_000_001, **payload_kwargs))

    (row,) = await store.execute("SELECT * FROM viewers WHERE account_id='v1'")
    assert {k: row[k] for k in expected} == expected
    assert (row["user_name"], row["last_active_ms"]) == ("观众甲", 1_700_000_000_001)
    # 明细行挂上内部观众 ID，并记下原始外部 ID 的种类
    table = {"danmaku": "live_chat", "gift": "gifts", "super_chat": "super_chats"}[payload_kwargs["message_type"]]
    (detail,) = await store.execute(f"SELECT viewer_id, id_kind FROM {table}")
    assert (detail["viewer_id"], detail["id_kind"]) == (row["id"], "account")


async def test_streamer_speech_persists_reply_link_and_replied_stat(
    ledger, store: SQLiteDatabase, event_bus: EventBus
) -> None:
    question = make_room_message(content="主播玩什么游戏？", message_id="msg_viewer_1")
    await _emit(event_bus, CoreEvents.ROOM_MESSAGE_DANMAKU, question)
    speech = _speech("今天玩《双人成行》！", target_user_id="tester", reply_to_message_id="msg_viewer_1")
    await _emit(event_bus, CoreEvents.STREAMER_SPEECH, speech)

    (row,) = await store.execute("SELECT * FROM live_chat WHERE message_type='speak'")
    assert (row["sender_role"], row["sender_name"], row["content"]) == ("assistant", "主播", "今天玩《双人成行》！")
    assert (row["simulated"], row["timestamp_ms"], row["live_session_id"]) == (0, 1_700_000_000_000, _FAKE_PK)
    # "主播回应了哪条弹幕"可关联查询
    joined = await store.execute(
        "SELECT a.content AS reply FROM live_chat a JOIN live_chat q ON a.reply_to_message_id = q.message_id "
        "WHERE q.message_id = 'msg_viewer_1'"
    )
    assert [r["reply"] for r in joined] == ["今天玩《双人成行》！"]

    (viewer,) = await store.execute("SELECT * FROM viewers WHERE account_id='tester'")
    assert (viewer["replied_count"], viewer["interaction_count"], viewer["message_count"]) == (1, 2, 1)

    # 回复一个从没发过言（未建档）的外部 ID：只落主播发言，不凭空建观众
    await _emit(event_bus, CoreEvents.STREAMER_SPEECH, _speech("你好陌生人", target_user_id="u_unknown"))
    assert len(await store.execute("SELECT * FROM viewers")) == 1


async def test_streamer_speech_insert_failure_is_swallowed(
    ledger, store: SQLiteDatabase, event_bus: EventBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    original_insert = ledger.chat_repo.insert_live_chat

    async def flaky_insert(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("模拟 SQLite 写失败")
        return await original_insert(*args, **kwargs)

    monkeypatch.setattr(ledger.chat_repo, "insert_live_chat", flaky_insert)
    await _emit(event_bus, CoreEvents.STREAMER_SPEECH, _speech("第一次会失败"))
    await _emit(event_bus, CoreEvents.STREAMER_SPEECH, _speech("第二次应落库"))

    rows = await store.execute("SELECT content FROM live_chat WHERE message_type='speak'")
    assert [r["content"] for r in rows] == ["第二次应落库"]
    # 无回复对象的发言不建观众行
    assert await store.execute("SELECT * FROM viewers") == []


@pytest.mark.parametrize(
    ("method", "event", "payload"),
    [
        ("resolve_viewer", CoreEvents.ROOM_MESSAGE_DANMAKU, make_room_message(content="upsert 会炸")),
        ("find_viewer_id", CoreEvents.STREAMER_SPEECH, _speech("upsert 会炸", target_user_id="u_exc")),
    ],
)
async def test_viewer_upsert_failure_keeps_main_row(
    ledger, store: SQLiteDatabase, event_bus: EventBus, monkeypatch: pytest.MonkeyPatch, method, event, payload
) -> None:
    async def boom(*args, **kwargs):
        raise RuntimeError("模拟 viewers upsert 失败")

    monkeypatch.setattr(ledger.viewer_repo, method, boom)
    await _emit(event_bus, event, payload)

    assert len(await store.execute("SELECT * FROM live_chat WHERE content='upsert 会炸'")) == 1
    assert await store.execute("SELECT * FROM viewers") == []


_UID_USER = RoomMessageUser(id="1001", name="老王")
_OPEN_USER = RoomMessageUser(id="39b8fedb-60a5-4e29-ac75-b16955f7e632", name="老王", id_kind="open", open_app_id="a")


async def test_switching_source_by_default_splits_and_suggests_merge(
    ledger, store: SQLiteDatabase, event_bus: EventBus
) -> None:
    """默认配置不按昵称自动关联：换源后同名的新 ID 先成为另一位观众，挂一条并入建议等运营者确认。"""
    await _emit(event_bus, CoreEvents.ROOM_MESSAGE_DANMAKU, make_room_message(user=_UID_USER, content="一"))
    await _emit(event_bus, CoreEvents.ROOM_MESSAGE_DANMAKU, make_room_message(user=_OPEN_USER, content="二"))

    old, new = await store.execute("SELECT * FROM viewers ORDER BY id")
    assert (old["account_id"], old["open_id"], old["message_count"]) == ("1001", None, 1)
    assert (new["open_id"], new["message_count"], new["link_status"], new["link_candidate_id"]) == (
        _OPEN_USER.id,
        1,
        "pending",
        old["id"],
    )


async def test_switching_source_keeps_one_viewer_by_name_when_enabled(
    make_ledger, store: SQLiteDatabase, event_bus: EventBus
) -> None:
    """使用者打开了按昵称关联（填了时限）：LAPLACE 源给账号 uid，换到官方开放平台源后只有 open_id，
    同名、时限内 → 仍是同一位观众。"""
    await make_ledger(name_match_window_ms=90 * 86_400_000).start()
    uid_user, open_user = _UID_USER, _OPEN_USER
    await _emit(event_bus, CoreEvents.ROOM_MESSAGE_DANMAKU, make_room_message(user=uid_user, content="一"))
    await _emit(event_bus, CoreEvents.ROOM_MESSAGE_DANMAKU, make_room_message(user=open_user, content="二"))

    (viewer,) = await store.execute("SELECT * FROM viewers")
    assert (viewer["account_id"], viewer["open_id"], viewer["message_count"]) == ("1001", open_user.id, 2)
    chat = await store.execute("SELECT sender_id, viewer_id, id_kind FROM live_chat ORDER BY id")
    assert [(r["sender_id"], r["viewer_id"], r["id_kind"]) for r in chat] == [
        ("1001", viewer["id"], "account"),
        (open_user.id, viewer["id"], "open"),  # 明细保留收到时的原始外部 ID 与种类
    ]


async def test_broken_gift_does_not_block_next_write(ledger, store: SQLiteDatabase, event_bus: EventBus) -> None:
    broken = RoomMessagePayload(
        live_session_id=0,
        message_type="gift",  # type: ignore[arg-type]
        user=RoomMessageUser(id="u", name="n"),
        content="",
        gift=None,
        timestamp_ms=1,
    )
    await _emit(event_bus, CoreEvents.ROOM_MESSAGE_GIFT, broken)
    await _emit(event_bus, CoreEvents.ROOM_MESSAGE_GIFT, make_room_message(message_type="gift", gift_name="Y"))

    assert "Y" in {r["gift_name"] for r in await store.execute("SELECT gift_name FROM gifts")}


async def test_game_events_persist_with_null_empty_scene(ledger, store: SQLiteDatabase, event_bus: EventBus) -> None:
    milestone = _game("milestone", "挖到钻石了！", scene="y=-12", timestamp_ms=1_700_000_000_020)
    await _emit(event_bus, "game.milestone", milestone)
    await _emit(event_bus, "game.attention_required", _game("attention_required"))
    await _emit(event_bus, "game.error", _game("error"))

    rows = await store.execute("SELECT * FROM game_events ORDER BY id")
    assert [r["event_type"] for r in rows] == ["milestone", "attention_required", "error"]
    first = rows[0]
    assert (first["live_session_id"], first["game"], first["message"]) == (_FAKE_PK, "minecraft", "挖到钻石了！")
    assert (first["scene"], first["timestamp_ms"]) == ("y=-12", 1_700_000_000_020)
    # scene 留空落 NULL，便于区分"无场景上下文"
    assert rows[1]["scene"] is None


async def test_game_events_keep_save_reported_by_publisher(ledger, store: SQLiteDatabase, event_bus: EventBus) -> None:
    """发布方填的存档原样落库；不分存档的游戏不填，落空串。"""
    await _emit(event_bus, "game.milestone", _game("milestone", save_id="ab12", save_name="新的世界"))
    await _emit(event_bus, "game.milestone", _game("milestone"))

    rows = await store.execute("SELECT save_id, save_name FROM game_events ORDER BY id")
    assert [(r["save_id"], r["save_name"]) for r in rows] == [("ab12", "新的世界"), ("", "")]


async def test_place_marks_land_as_place_rows_and_stay_out_of_game_progress(
    ledger, store: SQLiteDatabase, event_bus: EventBus
) -> None:
    """地点动静落成 place_* 行，只给下播整理认地点；按场读游戏进展、按类型计数都不含它们。"""
    await _emit(event_bus, "game.milestone", _game("milestone", "灯塔盖好了", timestamp_ms=1_000))
    near = GamePlacePayload(game="minecraft", kind="near", place="灯塔", distance=7, save_id="a", timestamp_ms=2_000)
    await _emit(event_bus, CoreEvents.GAME_PLACE_NOTED, near)
    await _emit(
        event_bus,
        CoreEvents.GAME_PLACE_NOTED,
        GamePlacePayload(game="minecraft", kind="remembered", place="家", timestamp_ms=3_000),
    )

    marks = await store.events.list_session_place_marks(live_session_id=_FAKE_PK)
    assert [(m.kind, m.place, m.distance, m.save_id) for m in marks] == [
        ("near", "灯塔", 7, "a"),
        ("remembered", "家", None, ""),
    ]
    progress = await store.events.list_session_game_events(live_session_id=_FAKE_PK)
    assert [record.message for record in progress] == ["灯塔盖好了"]
    counts = await store.events.count_game_events_by_type(live_session_ids=[_FAKE_PK])
    assert [(count.event_type, count.n) for count in counts] == [("milestone", 1)]


async def test_game_event_write_failure_is_swallowed(
    ledger, store: SQLiteDatabase, event_bus: EventBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(**kwargs):
        raise RuntimeError("db locked")

    monkeypatch.setattr(ledger.event_repo, "insert_game_event", boom)
    await _emit(event_bus, "game.error", _game("error", "写入会炸"))
    assert (await _counts(store))["game_events"] == 0


_ALL_EVENTS = [
    (CoreEvents.ROOM_MESSAGE_DANMAKU, make_room_message(content="弹幕")),
    (CoreEvents.ROOM_MESSAGE_GIFT, make_room_message(message_type="gift")),
    (CoreEvents.STREAMER_SPEECH, _speech("发言")),
    ("game.milestone", _game("milestone")),
]


async def test_enter_event_is_not_persisted(ledger, store: SQLiteDatabase, event_bus: EventBus) -> None:
    await _emit(event_bus, CoreEvents.ROOM_MESSAGE_ENTER, make_room_message(message_type="enter"))
    assert set((await _counts(store)).values()) == {0}


async def test_nothing_persists_without_any_attributable_session(make_ledger, store: SQLiteDatabase, event_bus) -> None:
    """一场都没结束过（没有可归属的场次）：消息只在内存流转，全部类型都不落库。"""
    await make_ledger(pk=None).start()
    for event, payload in _ALL_EVENTS:
        await _emit(event_bus, event, payload)
    assert set((await _counts(store)).values()) == {0}


async def test_post_session_speech_lands_in_last_ended_session(store: SQLiteDatabase, event_bus: EventBus) -> None:
    """场后发言（上一场已结束、本场未开）落进刚结束的那一场，不再凭空消失。

    走真实装配：盖章拦截器 + StorageLedger + LiveSessionManager——收播道别
    与场后观众消息记成 0 时会被落库路径整体跳过，统计里凭空少一段。
    """
    manager = LiveSessionManager(store.sessions, store.chat, event_bus, platform="bilibili")
    await manager.start()
    event_bus.add_interceptor(SessionStampInterceptor(manager))
    ledger = StorageLedger(
        event_bus=event_bus,
        chat_repo=store.chat,
        viewer_repo=store.viewers,
        event_repo=store.events,
        session_manager=manager,
    )
    await ledger.start()
    try:
        pk = await manager.open_session(kind="live")
        await _emit(event_bus, CoreEvents.ROOM_MESSAGE_DANMAKU, make_room_message(content="播中弹幕"))
        await manager.close_session()
        await event_bus.wait_until_drained()

        # 场后：上一场已结束、本场未开——道别发言归刚结束的那一场
        await _emit(event_bus, CoreEvents.STREAMER_SPEECH, _speech("今晚就到这儿吧"))

        rows = await store.execute(
            "SELECT live_session_id, content FROM live_chat WHERE message_type='speak' ORDER BY id"
        )
        assert [(r["live_session_id"], r["content"]) for r in rows] == [(pk, "今晚就到这儿吧")]
        # 播中那条弹幕仍归本场，不受场后归属影响
        chat = await store.execute("SELECT live_session_id, content FROM live_chat WHERE message_type='danmaku'")
        assert [(r["live_session_id"], r["content"]) for r in chat] == [(pk, "播中弹幕")]
    finally:
        await ledger.stop()


async def test_repeated_start_then_stop_unsubscribes_everything(make_ledger, store: SQLiteDatabase, event_bus) -> None:
    ledger = make_ledger()
    await ledger.start()
    await ledger.start()
    await ledger.stop()
    for event, payload in _ALL_EVENTS:
        await _emit(event_bus, event, payload)
    assert set((await _counts(store)).values()) == {0}
