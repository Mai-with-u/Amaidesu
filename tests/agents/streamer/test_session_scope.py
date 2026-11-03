"""SessionScope：场次作用域状态与决策窗开窗的接口级测试。

锁定的不变量：出账先于读取（open_window 内化的顺序契约）、读历史纯读、
合并投影按发生时刻稳定排序、读库失败宁可无历史、清场五件套、补记的
落库判定分支。时间线投影本体（project）有自己的测试，此处多用替身；
"道别那一轮开窗不把时间线当作换场清空"要看真实时间线的章节是否还在，那一条用真件。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerContextConfig
from src.agents.streamer.game_ledger import GameLedger
from src.agents.streamer.session_scope import LiveChatTurn, SessionScope, WindowMaterials
from src.agents.streamer.timeline import (
    CHAPTER_MESSAGE_TYPE,
    GameEventBatch,
    StreamerTimeline,
    TimelineTurn,
)
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.llm.payload import Response
from src.modules.storage.models.chat import LiveChatMessage
from tests.config.helpers import wait_until


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
    """聊天仓储替身：返回预置行，可注入异常；记下每次按哪一场读。"""

    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None, error: Optional[Exception] = None) -> None:
        self._rows = rows or []
        self._error = error
        self.read_sessions: List[int] = []

    async def list_recent_live_chat(self, *, live_session_id: int, limit: Optional[int]) -> List[LiveChatMessage]:
        self.read_sessions.append(live_session_id)
        if self._error is not None:
            raise self._error
        return self._rows


class _FakeSessionManager:
    def __init__(self, pk: Optional[int] = None, error: Optional[Exception] = None) -> None:
        # 公开属性：用例直接改它模拟开播与下播（进行中场次被清空）
        self.pk = pk
        self._error = error

    async def resolve_pk(self) -> Optional[int]:
        if self._error is not None:
            raise self._error
        return self.pk


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
            LiveChatMessage(
                id=1,
                live_session_id=7,
                timestamp_ms=100,
                sender_role="viewer",
                content="库里先问",
                sender_name="观众",
                message_type="danmaku",
                message_id="m1",
            )
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


# 收播道别：按正在结束的那一场读（进行中场次已清空）


def _row(content: str, ts: int) -> LiveChatMessage:
    return LiveChatMessage(
        id=ts,
        live_session_id=7,
        timestamp_ms=ts,
        sender_role="viewer",
        content=content,
        sender_name="观众",
        message_type="danmaku",
        message_id=f"m{ts}",
    )


async def test_closing_window_reads_the_ending_session() -> None:
    """场次已写成已结束、解析不到当前场次时，按给定的场次号读库，并以同一场次号交时间线投影。"""
    timeline, chat = _FakeTimeline(), _FakeChat(rows=[_row("TNT还差一排", 100)])
    manager = _FakeSessionManager(pk=7)
    scope = _scope(chat=chat, session_manager=manager, timeline=timeline)
    await scope.read_history()  # 直播中：时间线跟着第 7 场
    manager.pk = None  # 下播：场次管理器已清掉进行中场次

    materials = await scope.open_window(scope_pk=7)

    assert [turn.content for turn in materials.history] == ["TNT还差一排"]
    assert chat.read_sessions == [7, 7]
    assert [call["scope"] for call in timeline.project_calls] == [7, 7], "交给时间线的仍是同一场"


@pytest.mark.usefixtures("chars_as_tokens")  # 按字符数计 token：30 条约百字的弹幕越过 4000 预算的提前整理线
async def test_closing_window_keeps_chapter_written_during_the_session() -> None:
    """真实时间线：直播中后台写好的前情提要，道别那一轮按正在结束的那一场开窗时仍在。

    时间线只在场次号变了时整个清空（章节、在跑的整理一并作废）；道别那一轮若按"没开场次"
    开窗，这一章会丢，前情要从头再压一遍。
    """
    llm = MagicMock()
    llm.get_profile_context_window = MagicMock(return_value=0)
    llm.generate = AsyncMock(return_value=Response(success=True, content="阿杰一直催着把TNT阵铺完。"))
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    timeline = StreamerTimeline(
        budget=StreamerContextConfig(max_tokens=4000),
        llm_service=llm,
        prompt_service=prompt,
        profiles=("planner",),
        summary_profile="summary",
    )
    rows = [_row(f"第{i}句" + "铺" * 100, i) for i in range(1, 31)]
    manager = _FakeSessionManager(pk=7)
    scope = _scope(chat=_FakeChat(rows=rows), session_manager=manager, timeline=timeline)
    await scope.read_history()  # 直播中：越过提前整理线，后台开始写第一章前情提要
    await wait_until(lambda: llm.generate.await_count == 1, what="后台写完第一章前情提要")
    manager.pk = None  # 下播：场次管理器已清掉进行中场次

    materials = await scope.open_window(scope_pk=7)

    assert materials.history is not None
    first = materials.history[0]
    assert first.message_type == CHAPTER_MESSAGE_TYPE and "阿杰一直催着把TNT阵铺完" in first.content, (
        "直播中写好的那一章还在：同一场，时间线没被当作换场清空"
    )


async def test_closing_round_speech_is_not_kept_as_unpersisted_turn() -> None:
    """道别句带着正在结束那一场的编号落库，补记时不再留进内存对话（否则读历史会重复）。"""
    scope = _scope(chat=_FakeChat(), session_manager=_FakeSessionManager(pk=None))

    await scope.remember_round([], speech="下次继续炸TNT", batch_persisted=True, scope_pk=7)
    assert await scope.read_history(scope_pk=7) == []

    await scope.remember_round([], speech="下播后的闲聊", batch_persisted=True)
    assert [turn.content for turn in await scope.read_history()] == ["下播后的闲聊"], "其余轮次照旧按未落库补记"


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
