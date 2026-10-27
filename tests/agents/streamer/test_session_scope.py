"""SessionScope：场次作用域状态与决策窗开窗的接口级测试。

锁定的不变量：出账先于读取（open_window 内化的顺序契约）、读历史纯读、
合并投影按发生时刻稳定排序、读库失败宁可无历史、清场五件套、补记的
落库判定分支。时间线投影本体（project）有自己的测试，此处用替身。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from unittest.mock import MagicMock

from src.agents.streamer.game_ledger import GameLedger
from src.agents.streamer.session_scope import LiveChatTurn, SessionScope, WindowMaterials
from src.agents.streamer.timeline import GameEventBatch, TimelineTurn
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser


class _FakeTimeline:
    """时间线替身：project 透传返回（投影逻辑另有测试），reset 记录调用。"""

    def __init__(self) -> None:
        self.project_calls: List[Dict[str, Any]] = []
        self.reset_calls = 0

    async def project(self, turns: List[Any], *, scope: Optional[int]) -> List[Any]:
        self.project_calls.append({"turns": list(turns), "scope": scope})
        return list(turns)

    async def reset(self) -> None:
        self.reset_calls += 1


class _FakeChat:
    """聊天仓储替身：返回预置行，可注入异常。"""

    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None, error: Optional[Exception] = None) -> None:
        self._rows = rows or []
        self._error = error

    async def list_recent_live_chat(self, *, live_session_id: int, limit: Optional[int]) -> List[Dict[str, Any]]:
        if self._error is not None:
            raise self._error
        return self._rows


class _FakeSessionManager:
    def __init__(self, pk: Optional[int] = None, error: Optional[Exception] = None) -> None:
        self._pk = pk
        self._error = error

    async def resolve_pk(self) -> Optional[int]:
        if self._error is not None:
            raise self._error
        return self._pk


def _turn(content: str, ts: int, *, role: str = "viewer", message_type: str = "danmaku") -> LiveChatTurn:
    return LiveChatTurn(role=role, content=content, message_type=message_type, timestamp_ms=ts)


def _msg(text: str, ts: int) -> RoomMessagePayload:
    return RoomMessagePayload(
        message_type="danmaku",
        user=RoomMessageUser(id="u1", name="观众"),
        content=text,
        message_id=f"m{ts}",
        timestamp_ms=ts,
    )


def _scope(
    *,
    ledger: Any = None,
    chat: Any = None,
    session_manager: Any = None,
    timeline: Any = None,
) -> SessionScope:
    return SessionScope(
        ledger=ledger if ledger is not None else GameLedger(),
        timeline=timeline if timeline is not None else _FakeTimeline(),
        chat=chat,
        session_manager=session_manager,
        logger=MagicMock(),
    )


# 开窗：出账先于读取，且每窗恰好出账一次


async def test_open_window_drains_before_reading_history() -> None:
    calls: List[str] = []

    class _SpyLedger(GameLedger):
        def drain(self, private_chat: Any = ()) -> GameEventBatch:  # noqa: ANN001 - 签名随基类
            calls.append("drain")
            return super().drain(private_chat=private_chat)

    class _SpyTimeline(_FakeTimeline):
        async def project(self, turns: List[Any], *, scope: Optional[int]) -> List[Any]:
            calls.append("project")
            return list(turns)

    scope = _scope(ledger=_SpyLedger(), timeline=_SpyTimeline())

    materials = await scope.open_window()

    assert isinstance(materials, WindowMaterials)
    assert calls == ["drain", "project"], "出账先于读取——出账行才能进本轮投影"


async def test_second_open_window_drains_empty_batch() -> None:
    scope = _scope()
    ledger = scope.ledger
    ledger.record_event("minecraft", "milestone", "挖到钻石", 1000)

    first = await scope.open_window()
    second = await scope.open_window()

    assert first.game_batch.events != ()
    assert second.game_batch.events == (), "账本已出账，第二窗不再重放"


async def test_read_history_is_pure_read() -> None:
    scope = _scope()
    scope.ledger.record_event("minecraft", "milestone", "挖到钻石", 1000)
    await scope.read_history()

    batch_before = scope.current_window_batch()
    assert batch_before.events == (), "纯读不触发出账"
    assert await scope.read_history() == [], "纯读不落时间线：待出账的事件行不进投影"


# 读历史：合并投影与失败语义


async def test_read_history_merges_stored_and_unpersisted_by_timestamp() -> None:
    chat = _FakeChat(
        rows=[
            {
                "sender_role": "viewer",
                "content": "库里先问",
                "sender_name": "观众",
                "message_type": "danmaku",
                "message_id": "m1",
                "timestamp_ms": 100,
            }
        ]
    )
    scope = _scope(chat=chat, session_manager=_FakeSessionManager(pk=7))

    await scope.remember_round([_msg("内存后答之前观众又说", 200)], speech=None, batch_persisted=False)

    history = await scope.read_history()
    assert history is not None
    assert [turn.content for turn in history] == ["库里先问", "内存后答之前观众又说"]


async def test_read_history_returns_none_on_storage_failure() -> None:
    scope = _scope(
        chat=_FakeChat(error=RuntimeError("库坏了")),
        session_manager=_FakeSessionManager(pk=7),
    )

    assert await scope.read_history() is None, "读库失败宁可无历史，不拿残缺冒充完整"


# 清场：五个场次作用域状态归零


async def test_begin_and_end_session_reset_all_session_state() -> None:
    timeline = _FakeTimeline()
    scope = _scope(timeline=timeline)
    scope.ledger.record_event("minecraft", "milestone", "挖到钻石", 1000)
    scope.record_private_chat("[minecraft] 玩家（私聊，只发给我）：悄悄说")
    scope._unpersisted_turns.append(_turn("未落库", 1))
    scope._timeline_notes.append(
        TimelineTurn(role="user", content="游戏·minecraft·milestone", message_type="game", timestamp_ms=1000)
    )

    await scope.begin_session()
    assert timeline.reset_calls == 1
    assert await scope.read_history() == [], "未落库轮与时间线 notes 随开播清场归零"

    # 下播清场与开播同一份实现
    scope._timeline_notes.append(TimelineTurn(role="user", content="再记一条", message_type="game", timestamp_ms=2000))
    await scope.end_session()
    assert timeline.reset_calls == 2
    assert await scope.read_history() == []
    assert scope.current_window_batch().events == ()


# 补记：落库判定分支


async def test_remember_round_appends_unpersisted_viewer_and_speech_turns() -> None:
    scope = _scope()  # 无仓储 → 本轮对话必然不落库

    await scope.remember_round([_msg("主播好", 100)], speech="你好呀", batch_persisted=False)

    history = await scope.read_history()
    assert [(turn.role, turn.content) for turn in history] == [
        ("viewer", "主播好"),
        ("assistant", "你好呀"),
    ]


async def test_remember_round_skips_when_batch_and_speech_persisted() -> None:
    scope = _scope(chat=_FakeChat(), session_manager=_FakeSessionManager(pk=7))

    await scope.remember_round([_msg("已落库", 100)], speech="已落库的回复", batch_persisted=True)

    assert await scope.read_history() == []


async def test_remember_round_keeps_viewer_turns_when_batch_not_persisted() -> None:
    scope = _scope(chat=_FakeChat(), session_manager=_FakeSessionManager(pk=7))

    # 调试注入：开着场次也不经 room.message 落库链路
    await scope.remember_round([_msg("调试注入", 100)], speech="已落库的回复", batch_persisted=False)

    assert [turn.content for turn in await scope.read_history()] == ["调试注入"]


# 私聊与回顾槽位


async def test_private_chat_travels_with_window_batch_once() -> None:
    scope = _scope()
    scope.record_private_chat("[minecraft] 悄悄说")

    batch = scope.drain_window()

    assert batch.private_chat == ("[minecraft] 悄悄说",)
    assert scope.drain_window().private_chat == (), "送达一次制，下一窗不再携带"


async def test_recap_slot_flows_into_window_materials() -> None:
    scope = _scope()
    scope.set_session_recap("上场挖到钻石")

    materials = await scope.open_window()

    assert materials.session_recap == "上场挖到钻石"


async def test_note_delegation_writes_timeline_note_without_window_batch() -> None:
    scope = _scope()
    ledger = scope.ledger

    scope.note_delegation("minecraft", "去挖石头")

    materials = await scope.open_window()
    assert len(materials.timeline_notes) == 1
    batch = scope.drain_window()
    assert batch.events == (), "委派行不进决策窗批次"
    assert ledger is scope.ledger
