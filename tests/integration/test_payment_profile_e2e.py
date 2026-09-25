"""付费数据链路 + 平台身份 + 观众画像端到端测试

链路：room.message.* 事件 → StorageLedger 落库（付费三表全字段 + viewers
统计写穿）→ (platform, user_id) 身份隔离 → SimpleMemory 事实/画像 →
Planner 画像注入。

LLM 不在链内（mock），存储与事件总线走真实组件（SQLite 临时库）。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.background import BackgroundMaintainer
from src.agents.streamer.planner import Planner
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.logging import get_logger
from src.modules.memory.simple_memory import SimpleMemory
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.repos import ViewerRepo
from src.modules.storage.repos import ChatRepo, ViewerRepo
from src.modules.storage.storage_ledger import StorageLedger
from tests.modules.storage.helpers import make_room_message


def _user(user_id: str, user_name: str):
    from src.modules.events.payloads.room import RoomMessageUser

    return RoomMessageUser(id=user_id, name=user_name)


class _Resolver:
    """session_manager 最小替身：resolve_pk 返回固定场次主键。"""

    def __init__(self, resolve) -> None:
        self.resolve_pk = resolve

logger = get_logger("test_payment_profile_e2e")


@pytest.fixture
async def stack(temp_db_path: "pytest.FixtureRequest"):
    """真实事件总线 + 存储三件 + 记忆服务 + 记账器。"""
    store = SQLiteDatabase(temp_db_path)
    await store.initialize()
    bus = EventBus()
    memory = SimpleMemory(store)
    await memory.initialize()
    ledger = StorageLedger(
        bus,
        ChatRepo(store.manager),
        ViewerRepo(store.manager),
        store.events,
    )
    await ledger.start()
    try:
        yield store, bus, memory
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
    store, bus, _memory = stack

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
async def test_fact_to_profile_to_planner_injection(stack) -> None:
    """切片 D/E 验收：事实落库 → 达门槛生成画像 → planner 参考段出现画像；
    无画像观众不注入。"""
    store, bus, memory = stack

    # 观众达标（interaction_count=3：3 条弹幕）+ 2 条有信息量的弹幕
    for i in range(3):
        await _emit(
            bus,
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            make_room_message(
                live_session_id=1,
                message_id=f"m{i}",
                content=f"第{i}条弹幕",
                user=_user("u_veteran", "老王"),
            ),
        )
    # 互动量不足的观众
    await _emit(
        bus,
        CoreEvents.ROOM_MESSAGE_DANMAKU,
        make_room_message(
            live_session_id=1, message_id="m9", content="刚来问问", user=_user("u_new", "新人")
        ),
    )
    bili_viewers = await store.execute("SELECT interaction_count FROM viewers WHERE platform='bilibili'")
    interactions = {int(row["interaction_count"]) for row in bili_viewers}
    assert 3 in interactions and 1 in interactions

    # 后台事实提取（LLM mock 输出 JSON；归属经 message_id 程序化完成）
    room_state = __import__("src.agents.streamer.room_state", fromlist=["RoomState"]).RoomState()
    llm = _make_llm(
        '{"summary": "在聊直播安排", "facts": ['
        '{"message_id": "m0", "fact": "老王喜欢聊直播安排"}, '
        '{"message_id": "m_ghost", "fact": "幻觉事实"}]}'
    )
    prompt_manager = MagicMock()
    prompt_manager.render = MagicMock(return_value="PROMPT")
    session_stub = __import__(
        "unittest.mock", fromlist=["AsyncMock"]
    ).AsyncMock(return_value=1)  # resolve_pk → 1（事件归属的场次）
    maintainer = BackgroundMaintainer(
        config={},
        room_state=room_state,
        llm_service=llm,
        chat_repo=store.chat,  # 与落库同源的弹幕读取
        session_manager=_Resolver(session_stub),
        memory=memory,
        prompt_manager=prompt_manager,
    )
    # 直接驱动摘要任务（跳过循环与门控）
    await maintainer._summarize_topic(now_ms=10_000)

    veteran_facts = await memory.list_viewer_facts(platform="bilibili", user_id="u_veteran")
    assert len(veteran_facts) == 1, "幻觉 message_id 应被丢弃，只落 1 条合法事实"

    # 画像生成：老王互动量 3 达门槛；新人 1 不达（无事实也自然不生成）
    await maintainer._generate_profiles()
    veteran_profile = await memory.get_viewer_profile(platform="bilibili", user_id="u_veteran")
    assert veteran_profile and "老王" in veteran_profile
    assert await memory.get_viewer_profile(platform="bilibili", user_id="u_new") is None

    # Planner 注入：本批含老王 → 参考段出现"昵称: 画像"（昵称经 viewers 实时取，
    # 与弹幕侧同源可关联）；无画像观众不占位
    planner = Planner(
        config={},
        llm_service=_make_llm('{"speech": "ok"}'),
        prompt_service=prompt_manager,
        room_state=room_state,
        memory=memory,
        viewer_repo=ViewerRepo(store.manager),
    )
    batch = [
        make_room_message(live_session_id=1, content="又来了", user=_user("u_veteran", "老王")),
        make_room_message(live_session_id=1, content="首次发言", user=_user("u_new", "新人")),
    ]
    section = await planner._collect_person_profiles(batch)
    assert "人物画像" not in section  # 段标题由 Assembler 渲染，此处只含正文
    assert "- 老王: " in section
    assert "u_veteran" not in section  # 有昵称时不再显示 user_id
    assert "u_new" not in section and "新人" not in section


def _make_llm(response_text: str) -> object:
    """最小 LLM 管理器替身：``generate`` 返回固定文本。"""
    from unittest.mock import AsyncMock, MagicMock

    llm = MagicMock()
    response = MagicMock()
    response.success = True
    response.content = response_text
    llm.generate = AsyncMock(return_value=response)
    return llm

