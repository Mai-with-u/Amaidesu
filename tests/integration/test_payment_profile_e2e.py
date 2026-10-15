"""付费数据链路 + 平台身份 + 主播记忆端到端测试

链路：room.message.* 事件 → StorageLedger 落库（付费三表全字段 + viewers
统计写穿）→ (platform, user_id) 身份隔离 → 收播整理（常客名册 + 场次回顾）
→ Planner 注入人物画像与上场回顾。

LLM 不在链内（mock），存储与事件总线走真实组件（SQLite 临时库）。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.memory import RegularsRosterWriter, SessionRecapWriter
from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import RoomState
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.logging import get_logger
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.repos import ChatRepo, ViewerRepo
from src.modules.storage.storage_ledger import StorageLedger
from tests.modules.storage.helpers import make_room_message


def _user(user_id: str, user_name: str):
    from src.modules.events.payloads.room import RoomMessageUser

    return RoomMessageUser(id=user_id, name=user_name)


logger = get_logger("test_payment_profile_e2e")


@pytest.fixture
async def stack(temp_db_path: "pytest.FixtureRequest"):
    """真实事件总线 + 存储 + 记账器。"""
    store = SQLiteDatabase(temp_db_path)
    await store.initialize()
    bus = EventBus()
    ledger = StorageLedger(
        bus,
        ChatRepo(store.manager),
        ViewerRepo(store.manager),
        store.events,
    )
    await ledger.start()
    try:
        yield store, bus
    finally:
        await ledger.stop()
        await bus.cleanup()
        await store.close()


@pytest.fixture
def temp_db_path(tmp_path):
    return tmp_path / "e2e.db"


async def _emit(bus: EventBus, event_name: str, payload: RoomMessagePayload) -> None:
    await bus.emit(event_name, payload, source="e2e")
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_payment_flow_and_platform_isolation(stack) -> None:
    """切片 A/B/C 验收：付费三事件落全字段；B 站与 console 数据按身份键隔离；
    付费统计只计金瓜子。"""
    store, bus = stack

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
    assert int(guard["total_price"]) == 138_000
    # live_chat 保留 guard 文本行
    guard_chat = await store.execute("SELECT * FROM live_chat WHERE message_type='guard'")
    assert len(guard_chat) == 1

    # 平台身份隔离：同 user_id 的 B 站行与 console 行各一行
    viewers = await store.execute(
        "SELECT * FROM viewers WHERE user_id='u_bili' ORDER BY platform"
    )
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
    total = await store.execute(
        "SELECT SUM(total_price) AS s FROM super_chats WHERE currency='bilibili_gold_coin'"
    )
    assert int(total[0]["s"]) == 50_000


@pytest.mark.asyncio
async def test_session_settle_to_roster_and_recap_to_planner(stack) -> None:
    """收播整理验收：本场达门槛的常客建档 → 本场写回顾 → 下一场 Planner 参考段
    出现"昵称: 印象"与上场回顾；互动不够的观众不建档、不注入。"""
    store, bus = stack
    pk = await store.sessions.insert_live_session(stream_id="room", platform="bilibili", started_at_ms=1_000)

    # 老王 3 条弹幕（互动量达门槛 3）；新人 1 条
    for i in range(3):
        await _emit(
            bus,
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            make_room_message(
                live_session_id=pk, message_id=f"m{i}", content=f"今晚想看你挖钻石{i}", user=_user("u_veteran", "老王")
            ),
        )
    await _emit(
        bus,
        CoreEvents.ROOM_MESSAGE_DANMAKU,
        make_room_message(live_session_id=pk, message_id="m9", content="刚来问问", user=_user("u_new", "新人")),
    )
    await store.chat.insert_live_chat(
        live_session_id=pk,
        timestamp_ms=5_000,
        sender_role="assistant",
        sender_id="麦麦",
        sender_name="麦麦",
        content="好，下次开播我就去挖钻石",
        message_type="speech",
    )
    await store.sessions.close_live_session(live_session_id=pk, ended_at_ms=9_000)

    prompt_manager = MagicMock()
    prompt_manager.render = MagicMock(return_value="PROMPT")
    roster_llm = _make_llm("老王：常来，想看主播挖钻石。")
    roster = RegularsRosterWriter(
        chat_repo=store.chat,
        viewer_repo=store.viewers,
        profile_repo=store.viewer_profiles,
        llm_service=roster_llm,
        prompt_manager=prompt_manager,
    )
    assert await roster.update_for_session(pk) == 1
    assert await store.viewer_profiles.get_profile_text(platform="bilibili", user_id="u_new") is None
    roster_prompt = roster_llm.generate.await_args.args[0]
    assert "今晚想看你挖钻石0" in roster_prompt  # 原料是本人原话

    recap_llm = _make_llm("这场我答应老王下次开播去挖钻石。")
    recap = SessionRecapWriter(
        sessions_repo=store.sessions,
        chat_repo=store.chat,
        recap_repo=store.recaps,
        topic_repo=store.topics,
        event_repo=store.events,
        llm_service=recap_llm,
        prompt_manager=prompt_manager,
    )
    assert await recap.write_recap(pk)
    recap_prompt = recap_llm.generate.await_args.args[0]
    assert "好，下次开播我就去挖钻石" in recap_prompt  # 主播自己说过的话进原料
    assert "老王（3 条）" in recap_prompt

    # 下一场：Planner 参考段同时带上场回顾与老王的印象（昵称经 viewers 实时取）
    planner = Planner(
        config={},
        llm_service=_make_llm('{"speech": "ok"}'),
        prompt_service=prompt_manager,
        room_state=RoomState(),
        profile_repo=store.viewer_profiles,
        viewer_repo=ViewerRepo(store.manager),
    )
    batch = [
        make_room_message(live_session_id=2, content="又来了", user=_user("u_veteran", "老王")),
        make_room_message(live_session_id=2, content="首次发言", user=_user("u_new", "新人")),
    ]
    section = await planner._collect_person_profiles(batch)
    assert "- 老王: 老王：常来，想看主播挖钻石。" in section
    assert "新人" not in section and "u_new" not in section

    recap_section = await recap.render_recent(count=1, exclude_session_id=None)
    assert "这场我答应老王下次开播去挖钻石。" in recap_section


def _make_llm(response_text: str) -> object:
    """最小 LLM 管理器替身：``generate`` 返回固定文本。"""
    from unittest.mock import AsyncMock, MagicMock

    llm = MagicMock()
    response = MagicMock()
    response.success = True
    response.content = response_text
    llm.generate = AsyncMock(return_value=response)
    return llm

