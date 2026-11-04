"""付费数据链路 + 平台身份隔离端到端测试。

链路：room.message.* 事件 → StorageLedger 落库（付费三表全字段 + viewers 统计
写穿）→ (platform, user_id) 身份隔离。存储与事件总线走真实组件（临时 SQLite 库
+ 真实 EventBus），LLM 不在链内。
"""

from __future__ import annotations

from typing import AsyncGenerator

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.storage_ledger import StorageLedger
from tests.modules.storage.helpers import make_room_message


@pytest.fixture()
async def ledger_bus(store: SQLiteDatabase, event_bus: EventBus) -> AsyncGenerator[EventBus, None]:
    """真实事件总线 + 真实记账器（写临时库），用例只经事件入口驱动。"""
    ledger = StorageLedger(event_bus, store.chat, store.viewers, store.events)
    await ledger.start()
    try:
        yield event_bus
    finally:
        await ledger.stop()


def _user(user_id: str, user_name: str) -> RoomMessageUser:
    return RoomMessageUser(id=user_id, name=user_name)


async def _emit(bus: EventBus, event_name: str, payload: RoomMessagePayload) -> None:
    await bus.emit(event_name, payload, source="e2e")
    await bus.wait_until_drained()


async def test_payment_flow_and_platform_isolation(store: SQLiteDatabase, ledger_bus: EventBus) -> None:
    """付费三事件落全字段；B 站与 console 数据按身份键隔离；付费统计只计金瓜子。"""
    bus = ledger_bus

    # B 站：弹幕 + 礼物（3×1000 金瓜子）+ SC（50 元 = 50000 金瓜子）+ 上舰
    await _emit(
        bus,
        CoreEvents.ROOM_MESSAGE_DANMAKU,
        make_room_message(live_session_id=1, content="主播好", user=_user("u_bili", "B站观众")),
    )
    await _emit(
        bus,
        CoreEvents.ROOM_MESSAGE_GIFT,
        make_room_message(
            live_session_id=1,
            message_type="gift",
            gift_name="小星星",
            gift_count=3,
            user=_user("u_bili", "B站观众"),
        ),
    )
    await _emit(
        bus,
        CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
        make_room_message(
            live_session_id=1,
            message_type="super_chat",
            content="SC 加油",
            sc_amount=50.0,
            user=_user("u_bili", "B站观众"),
        ),
    )
    await _emit(
        bus,
        CoreEvents.ROOM_MESSAGE_GUARD,
        make_room_message(
            live_session_id=1,
            message_type="guard",
            content="B站观众 开通了舰长",
            user=_user("u_bili", "B站观众"),
        ),
    )
    # console 调试数据：同 user_id 不同平台 → viewers 隔离
    await _emit(
        bus,
        CoreEvents.ROOM_MESSAGE_DANMAKU,
        make_room_message(
            live_session_id=1, content="调试弹幕", user=_user("u_bili", "控制台用户"), platform="console"
        ),
    )

    # gifts / super_chats / guards 全字段落库
    gift = (await store.execute("SELECT * FROM gifts"))[0]
    assert int(gift["quantity"]) == 3
    assert int(gift["unit_price"]) == 1000
    assert int(gift["total_price"]) == 3000
    assert str(gift["currency"]) == "bilibili_gold_coin"
    assert str(gift["platform"]) == "bilibili"

    sc = (await store.execute("SELECT * FROM super_chats"))[0]
    assert int(sc["total_price"]) == 50_000
    assert str(sc["currency"]) == "bilibili_gold_coin"

    guard = (await store.execute("SELECT * FROM guards"))[0]
    assert int(guard["guard_level"]) == 3
    assert int(guard["guard_num"]) == 1
    assert str(guard["guard_unit"]) == "月"
    assert int(guard["total_price"]) == 138_000
    assert str(guard["currency"]) == "bilibili_gold_coin"
    assert (str(guard["platform"]), str(guard["user_id"])) == ("bilibili", "u_bili")
    # live_chat 保留 guard 文本行
    guard_chat = await store.execute("SELECT * FROM live_chat WHERE message_type='guard'")
    assert len(guard_chat) == 1

    # 平台身份隔离：同 user_id 的 B 站行与 console 行各一行
    viewers = await store.execute("SELECT * FROM viewers WHERE account_id='u_bili' ORDER BY platform")
    assert len(viewers) == 2
    by_platform = {row["platform"]: row for row in viewers}
    bili = by_platform["bilibili"]
    console = by_platform["console"]

    # 付费统计：礼物 3000 + SC 50000 + 上舰 138000 = 191000 金瓜子；3 次
    assert int(bili["paid_count"]) == 3
    assert int(bili["paid_amount"]) == 191_000
    assert int(bili["message_count"]) == 1
    assert int(bili["gift_count"]) == 1
    assert int(bili["interaction_count"]) == 2  # 弹幕 + 送礼（SC 不落 live_chat 不计发言）

    # console 调试数据：金额载荷为空币种 → 不计付费
    assert int(console["paid_count"]) == 0
    assert int(console["paid_amount"]) == 0
    assert int(console["message_count"]) == 1

    # 平台内付费聚合可直接 SUM（同单位）
    total = await store.execute("SELECT SUM(total_price) AS s FROM super_chats WHERE currency='bilibili_gold_coin'")
    assert int(total[0]["s"]) == 50_000
