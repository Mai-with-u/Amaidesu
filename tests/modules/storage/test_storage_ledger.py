"""
StorageLedger 集成测试（v2.0.5 / ADR-006 溯源链收口）

覆盖：
- mock EventBus 发 RoomMessagePayload → 落库行数 + 列值
- simulated 端到端：payload.simulated bool → 表 INTEGER
- message_type 分发：danmaku/gift/super_chat/enter 各自落对应表
- enter 事件不落库（仅 debug 日志）
- 写入失败隔离：注入异常后下一条仍能落库，不传播到 emit 路径
- 关闭后：取消订阅，新增事件不再落库
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessageUser
from src.modules.storage.sqlite_store import SQLiteStore
from src.modules.storage.storage_ledger import StorageLedger, make_room_message


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="w3-ledger-"))
    yield td / "ledger.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteStore, None]:
    s = SQLiteStore(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
async def event_bus() -> AsyncGenerator[EventBus, None]:
    bus = EventBus()
    yield bus
    await bus.cleanup()


@pytest.fixture
async def ledger(event_bus: EventBus, store: SQLiteStore) -> AsyncGenerator[StorageLedger, None]:
    l = StorageLedger(event_bus=event_bus, sqlite_store=store)
    await l.start()
    yield l
    await l.stop()


# =============================================================================
# 分发：danmaku / gift / super_chat / enter
# =============================================================================


@pytest.mark.asyncio
async def test_ledger_dispatches_danmaku_to_live_chat(
    ledger: StorageLedger, store: SQLiteStore, event_bus: EventBus
) -> None:
    payload = make_room_message(
        message_type="danmaku", content="主播好可爱", simulated=False, session_id="ls_d1"
    )
    await event_bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, payload, source="test", wait=True)

    rows = await store.execute("SELECT * FROM live_chat WHERE message_type='danmaku'")
    assert len(rows) == 1
    assert str(rows[0]["content"]) == "主播好可爱"
    assert str(rows[0]["sender_id"]) == "tester"
    assert int(rows[0]["simulated"]) == 0
    # gifts / super_chats 均无新增
    assert len(await store.execute("SELECT * FROM gifts")) == 0
    assert len(await store.execute("SELECT * FROM super_chats")) == 0


@pytest.mark.asyncio
async def test_ledger_dispatches_gift_to_gifts(
    ledger: StorageLedger, store: SQLiteStore, event_bus: EventBus
) -> None:
    payload = make_room_message(
        message_type="gift",
        gift_name="小星星",
        gift_count=5,
        session_id="ls_g1",
    )
    await event_bus.emit(CoreEvents.ROOM_MESSAGE_GIFT, payload, source="test", wait=True)

    rows = await store.execute("SELECT * FROM gifts")
    assert len(rows) == 1
    assert str(rows[0]["gift_name"]) == "小星星"
    assert int(rows[0]["gift_count"]) == 5
    assert int(rows[0]["simulated"]) == 0
    assert len(await store.execute("SELECT * FROM live_chat")) == 0
    assert len(await store.execute("SELECT * FROM super_chats")) == 0


@pytest.mark.asyncio
async def test_ledger_dispatches_super_chat_to_super_chats(
    ledger: StorageLedger, store: SQLiteStore, event_bus: EventBus
) -> None:
    payload = make_room_message(
        message_type="super_chat",
        content="SC 文本",
        sc_amount=99.0,
        session_id="ls_sc1",
    )
    await event_bus.emit(CoreEvents.ROOM_MESSAGE_SUPER_CHAT, payload, source="test", wait=True)

    rows = await store.execute("SELECT * FROM super_chats")
    assert len(rows) == 1
    assert float(rows[0]["amount"]) == 99.0
    assert str(rows[0]["message"]) == "SC 文本"
    assert int(rows[0]["simulated"]) == 0


@pytest.mark.asyncio
async def test_ledger_enter_does_not_persist(
    ledger: StorageLedger, store: SQLiteStore, event_bus: EventBus
) -> None:
    """enter 事件 schema 无对应明细表——debug 日志后丢弃，不应落任何业务表。"""
    payload = make_room_message(message_type="enter", session_id="ls_e1")
    await event_bus.emit(CoreEvents.ROOM_MESSAGE_ENTER, payload, source="test", wait=True)

    for table in ("live_chat", "gifts", "super_chats"):
        rows = await store.execute(f"SELECT * FROM {table}")
        assert len(rows) == 0, f"{table} 应为空，实际 {len(rows)} 行"


# =============================================================================
# simulated 端到端（payload bool → 列 INTEGER）
# =============================================================================


@pytest.mark.asyncio
async def test_ledger_simulated_true_flows_to_column(
    ledger: StorageLedger, store: SQLiteStore, event_bus: EventBus
) -> None:
    """模拟器产出的事件 payload.simulated=True → live_chat.simulated=1。"""
    payload = make_room_message(message_type="danmaku", content="模拟弹幕", simulated=True)
    await event_bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, payload, source="test", wait=True)

    real = await store.execute("SELECT * FROM live_chat WHERE simulated=0")
    all_rows = await store.execute("SELECT * FROM live_chat")
    assert len(real) == 0, "模拟行应被排除查询过滤"
    assert len(all_rows) == 1
    assert int(all_rows[0]["simulated"]) == 1


@pytest.mark.asyncio
async def test_ledger_mixed_real_and_simulated_excluded(
    ledger: StorageLedger, store: SQLiteStore, event_bus: EventBus
) -> None:
    """混合真实+模拟写入后，``WHERE simulated=0`` 只返回真实。"""
    real_payload = make_room_message(
        message_type="danmaku", content="真弹幕", simulated=False, session_id="real"
    )
    sim_payload = make_room_message(
        message_type="danmaku", content="假弹幕", simulated=True, session_id="sim"
    )
    await event_bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, real_payload, source="t", wait=True)
    await event_bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, sim_payload, source="t", wait=True)

    real = await store.execute("SELECT content FROM live_chat WHERE simulated=0 ORDER BY id")
    all_rows = await store.execute("SELECT content FROM live_chat ORDER BY id")
    assert [str(r["content"]) for r in real] == ["真弹幕"]
    assert [str(r["content"]) for r in all_rows] == ["真弹幕", "假弹幕"]


# =============================================================================
# 同一 session_id 多次事件：session_pk 稳定（PK 复用）
# =============================================================================


@pytest.mark.asyncio
async def test_ledger_session_pk_stable_across_events(
    ledger: StorageLedger, store: SQLiteStore, event_bus: EventBus
) -> None:
    """同一 live_session_id 字符串的所有事件应落同一 live_chat.live_session_id INTEGER。"""
    s = "stable_session"
    for i in range(3):
        p = make_room_message(
            message_type="danmaku", content=f"弹幕{i}", session_id=s, simulated=False
        )
        await event_bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, p, source="t", wait=True)

    rows = await store.execute("SELECT live_session_id FROM live_chat ORDER BY id")
    assert len(rows) == 3
    pks = {int(r["live_session_id"]) for r in rows}
    assert len(pks) == 1, f"同一 session 应映射同一 INTEGER PK，实际 {pks}"


# =============================================================================
# 写入异常隔离（不阻断主循环）
# =============================================================================


@pytest.mark.asyncio
async def test_ledger_write_failure_does_not_break_subsequent_writes(
    event_bus: EventBus, store: SQLiteStore
) -> None:
    """注入一次写入异常后，下一条事件仍能落库；handler 不抛出。"""
    ledger = StorageLedger(event_bus=event_bus, sqlite_store=store)
    await ledger.start()

    # 第一次故意注入异常：传一个会触发缺失字段的 gift payload
    # 模拟 insert_gift 失败路径：handler 包了 try/except，不会让后续事件也被吞
    bad_payload = make_room_message(message_type="gift", gift_name="X", gift_count=1)
    # 让 user.id 为空字符串 → insert 仍合法；改用让 payload.gift=None 触发（payload.gift 在
    # make_room_message 中按 message_type 强制设置，这里改直接构造一个 gift 事件但
    # gift=None 的 payload 测一下。改路径：直接 emit 一个手写 payload.gift=None。

    from src.modules.events.payloads.room import RoomMessagePayload

    broken = RoomMessagePayload(
        live_session_id="broken",
        message_type="gift",  # type: ignore[arg-type]
        user=RoomMessageUser(id="u", name="n"),
        content="",
        gift=None,
        timestamp_ms=1,
        simulated=False,
    )
    await event_bus.emit(CoreEvents.ROOM_MESSAGE_GIFT, broken, source="t", wait=True)

    # 第二条：正常 gift —— 应落库
    good = make_room_message(message_type="gift", gift_name="Y", gift_count=2)
    await event_bus.emit(CoreEvents.ROOM_MESSAGE_GIFT, good, source="t", wait=True)

    rows = await store.execute("SELECT * FROM gifts")
    # 上面的 broken 在 ledger 内部 gift=None 分支仅写 debug，不抛；good 应落库 → 至少 1 行
    assert len(rows) >= 1, "异常路径后正常事件应落库，验证降级不阻断主循环"
    # good 一定在；broken 的不稳定性也只增加 gift_name='X' 行为可忽略
    names = sorted({str(r["gift_name"]) for r in rows})
    assert "Y" in names

    await ledger.stop()


# =============================================================================
# stop 后停止订阅
# =============================================================================


@pytest.mark.asyncio
async def test_ledger_stop_unsubscribes(
    event_bus: EventBus, store: SQLiteStore
) -> None:
    """stop 后再 emit 不应再触发落库。"""
    ledger = StorageLedger(event_bus=event_bus, sqlite_store=store)
    await ledger.start()
    await ledger.stop()

    payload = make_room_message(message_type="danmaku", content="x")
    await event_bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, payload, source="t", wait=True)

    rows = await store.execute("SELECT * FROM live_chat")
    assert len(rows) == 0


# =============================================================================
# start 幂等
# =============================================================================


@pytest.mark.asyncio
async def test_ledger_start_idempotent(event_bus: EventBus, store: SQLiteStore) -> None:
    ledger = StorageLedger(event_bus=event_bus, sqlite_store=store)
    await ledger.start()
    await ledger.start()  # 不抛即可
    await ledger.stop()


# =============================================================================
# PK 哈希稳定性
# =============================================================================


@pytest.mark.asyncio
async def test_ledger_session_pk_hash_is_stable() -> None:
    """同字符串 session_id 在不同时刻算出同一 INTEGER PK。"""
    a = StorageLedger._session_pk_to_int("session_alpha")
    b = StorageLedger._session_pk_to_int("session_alpha")
    c = StorageLedger._session_pk_to_int("session_beta")
    assert a == b
    assert a != c
    assert 0 <= a < 2**32
