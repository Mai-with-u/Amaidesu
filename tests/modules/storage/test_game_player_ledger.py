"""游戏里的人落库器：按 UUID 认人，公屏聊天只落公屏，互动记到对的人头上，公屏聊天受保留期管。

认人口径（代码行为，全部要满分）：
- 带 UUID、游戏核对过在线的发言人才建玩家；没有 UUID 或没核对过的只占一行位置、不存原话
- 私聊、系统消息不落库；没归属场次的事件不落库
- 肢体动作、切磋各记一条对的互动；切磋只有名字时找本场唯一同名玩家，对不上不记
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator, Tuple

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameChatPayload, GameSelfChattedPayload
from src.modules.events.payloads.game_player import GamePlayerInteractedPayload
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.game_player_ledger import GamePlayerLedger
from src.modules.storage.privacy import PrivacyService
from src.modules.storage.retention import RetentionService

_ALEX = "0f0e0d0c-1111-2222-3333-444455556666"


@pytest.fixture()
async def wired(tmp_path: Path) -> AsyncIterator[Tuple[SQLiteDatabase, EventBus, int]]:
    """接好落库器的库与事件总线，外加一场已开播的场次。"""
    store = SQLiteDatabase(tmp_path / "data" / "ledger.db")
    await store.initialize()
    bus = EventBus()
    ledger = GamePlayerLedger(bus, store.game_players)
    await ledger.start()
    session_id = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    try:
        yield store, bus, session_id
    finally:
        await ledger.stop()
        await bus.cleanup()
        await store.close()


def _chat(session_id: int, content: str, **kwargs: object) -> GameChatPayload:
    fields = {"game": "minecraft", "kind": "player", "content": content, "live_session_id": session_id}
    fields.update(kwargs)
    return GameChatPayload(**fields)  # type: ignore[arg-type]


async def test_only_checked_players_get_a_record_and_others_only_hold_a_place(wired) -> None:
    store, bus, session_id = wired
    for payload in (
        _chat(session_id, "来切磋", sender="Alex", sender_id=_ALEX, sender_online=True, save_id="w1"),
        # 控制台借名发的：没有 UUID，也没核对过
        _chat(session_id, "[Server] 重启", sender="Server"),
        # 有 UUID 但没核对过在线：不建玩家
        _chat(session_id, "我是 Alex", sender="Alex", sender_id="uuid-fake", sender_online=False),
        _chat(session_id, "悄悄话", sender="Alex", sender_id=_ALEX, sender_online=True, private=True),
        _chat(session_id, "Alex 加入了游戏", kind="system"),
        _chat(0, "没开播时说的", sender="Alex", sender_id=_ALEX, sender_online=True),
    ):
        # 事件总线并发扇出：逐条等落库，聊天行的先后才和发出的先后一致
        await bus.emit(CoreEvents.GAME_CHAT_RECEIVED, payload, source="test")
        await bus.wait_until_drained()
    await bus.emit(
        CoreEvents.GAME_SELF_CHATTED,
        GameSelfChattedPayload(game="minecraft", message="来就来", live_session_id=session_id),
        source="test",
    )
    await bus.wait_until_drained()

    (alex,) = await store.game_players.list_players()
    assert (alex.uuid, alex.name, [save.save_id for save in alex.saves]) == (_ALEX, "Alex", ["w1"])
    lines = await store.game_players.list_session_game_chat(live_session_id=session_id)
    assert [(line.direction, line.player_id, line.content) for line in lines] == [
        ("player", alex.id, "来切磋"),
        ("other", None, ""),
        ("other", None, ""),
        ("streamer", None, "来就来"),
    ]


async def test_body_language_and_spars_land_on_the_right_player(wired) -> None:
    store, bus, session_id = wired
    common = {"game": "minecraft", "live_session_id": session_id}
    for payload in (
        GamePlayerInteractedPayload(
            kind="greet", player_uuid=_ALEX, player_name="Alex", actions=["蹲起", "挥手"], **common
        ),
        GamePlayerInteractedPayload(kind="greet", player_uuid=_ALEX, player_name="Alex", actions=["蹲起"], **common),
        GamePlayerInteractedPayload(
            kind="gift", player_uuid=_ALEX, player_name="Alex", fact={"said": "朝我扔了 3 个面包"}, **common
        ),
        # 切磋只有名字：本场见过的 Alex 只有一位，记到他头上；没见过的 Steve 对不上，不记
        GamePlayerInteractedPayload(kind="spar", player_name="Alex", fact={"result": "won"}, **common),
        GamePlayerInteractedPayload(kind="spar", player_name="Steve", fact={"result": "lost"}, **common),
    ):
        await bus.emit(CoreEvents.GAME_PLAYER_INTERACTED, payload, source="test")
        await bus.wait_until_drained()

    (alex,) = await store.game_players.list_players()
    rows = await store.game_players.list_interactions(player_id=alex.id)
    facts = sorted((row.kind, tuple(sorted(row.fact.items(), key=str))) for row in rows)
    assert facts == [
        ("gift", (("said", "朝我扔了 3 个面包"),)),
        ("greet", (("actions", {"蹲起": 2, "挥手": 1}), ("count", 2))),
        ("spar", (("result", "won"),)),
    ]


async def test_public_chat_expires_with_the_session_but_her_lines_stay(wired, tmp_path: Path) -> None:
    store, bus, session_id = wired
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=2_000)
    await bus.emit(
        CoreEvents.GAME_CHAT_RECEIVED,
        _chat(session_id, "来切磋", sender="Alex", sender_id=_ALEX, sender_online=True),
        source="test",
    )
    await bus.emit(CoreEvents.GAME_CHAT_RECEIVED, _chat(session_id, "[Server] 重启", sender="Server"), source="test")
    await bus.emit(
        CoreEvents.GAME_SELF_CHATTED,
        GameSelfChattedPayload(game="minecraft", message="来就来", live_session_id=session_id),
        source="test",
    )
    await bus.wait_until_drained()

    privacy = PrivacyService(store.manager, db_path=store.db_path, key_path=tmp_path / "secrets" / "forget.key")
    await privacy.initialize()
    try:
        retention = RetentionService(store.manager, privacy, db_path=store.db_path, raw_days=1, clock=lambda: 10**12)
        result = await retention.run_cleanup()
    finally:
        await privacy.close()

    lines = await store.game_players.list_session_game_chat(live_session_id=session_id)
    assert [(line.direction, line.content) for line in lines] == [("streamer", "来就来")]
    assert result.chat_deleted == 2
    # 人还认得：保留期只清聊天这类工作数据
    assert len(await store.game_players.list_players()) == 1
