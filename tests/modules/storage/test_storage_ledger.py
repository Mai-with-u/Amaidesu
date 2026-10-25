"""StorageLedger 集成测试：事件分发落库、观众统计写穿、场次归属与失败隔离"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.storage_ledger import StorageLedger
from tests.modules.storage.helpers import make_room_message

# 场次管理器替身解析出的场次主键（模拟"显式场次进行中"）
_FAKE_PK = 4242
_TABLES = ("live_chat", "gifts", "super_chats", "game_events")


class _FakeSessionManager:
    def __init__(self, pk: Optional[int]) -> None:
        self._pk = pk
        # 主播发言行的平台归属（与采集器的 platform 一致，被回复计数据此找观众）
        self.platform = "bilibili"

    async def resolve_pk(self) -> Optional[int]:
        return self._pk


@pytest.fixture
async def make_ledger(event_bus: EventBus, store: SQLiteDatabase):
    created: list[StorageLedger] = []

    def _make(pk: Optional[int] = _FAKE_PK) -> StorageLedger:
        ledger = StorageLedger(
            event_bus=event_bus,
            chat_repo=store.chat,
            viewer_repo=store.viewers,
            event_repo=store.events,
            session_manager=_FakeSessionManager(pk),
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
    await asyncio.sleep(0.05)


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


async def test_switching_source_keeps_one_viewer_by_name(ledger, store: SQLiteDatabase, event_bus: EventBus) -> None:
    # LAPLACE 源给账号 uid，换到官方开放平台源后只有 open_id：同名、时限内 → 仍是同一位观众
    uid_user = RoomMessageUser(id="1001", name="老王")
    open_user = RoomMessageUser(id="39b8fedb-60a5-4e29-ac75-b16955f7e632", name="老王", id_kind="open", open_app_id="a")
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


async def test_nothing_persists_without_active_session(make_ledger, store: SQLiteDatabase, event_bus) -> None:
    """resolve_pk() 返回 None：消息只在内存流转，全部类型都不落库。"""
    await make_ledger(pk=None).start()
    for event, payload in _ALL_EVENTS:
        await _emit(event_bus, event, payload)
    assert set((await _counts(store)).values()) == {0}


async def test_repeated_start_then_stop_unsubscribes_everything(make_ledger, store: SQLiteDatabase, event_bus) -> None:
    ledger = make_ledger()
    await ledger.start()
    await ledger.start()
    await ledger.stop()
    for event, payload in _ALL_EVENTS:
        await _emit(event_bus, event, payload)
    assert set((await _counts(store)).values()) == {0}
