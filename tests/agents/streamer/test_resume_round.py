"""断线续播窗：中断场次接回后，主播按本场历史接续对话、排一轮接回发言；普通开播不排。

回归用真实的场次管理器、场次盖章拦截器与记账器串起来：残留场次经 ``resume_session``
接回后 ``live.started`` 带 ``resumed=True``，主播回调不清场（断前的对话历史在库里按
场次主键接着读）、流程单快照照常还原、再排一轮 RESUME 触发因的接回发言——接回句
落进续播的那一场。普通开播（``open_session``）不排接回轮。

本文件不用 ``EventBus.wait_until_drained`` 等派发落地（同 test_signoff_round 的注解：
Python 3.12 下有同步空转卡死风险），改用共享等待助手 ``wait_until`` 等条件成立。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.delegation_workbench import PLANNER_CALL_SOURCE
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.events.event_bus import EventBus
from src.modules.events.interceptors.session_stamp import SessionStampInterceptor
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveStartedPayload
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.llm.payload import Response, ToolCall
from src.modules.session.manager import LiveSessionManager
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.repos import ChatRepo
from src.modules.storage.storage_ledger import StorageLedger
from src.modules.tools import ToolRegistry
from tests.agents.streamer.helpers import FakeRoundExecutor, make_streamer_agent, stub_prompt
from tests.config.helpers import wait_until

# 登记（驱动/只读穿透，合并时进底账）：无。本文件全部经公开事件面与返回值驱动与观测。

RESUME_LINE = "信号回来了，我们接着聊！"

_planner_requests: List[str] = []


@dataclass
class _LiveStack:
    store: SQLiteDatabase
    bus: EventBus
    manager: LiveSessionManager
    writes: _LedgerChatWrites
    speeches: List[StreamerSpeechPayload] = field(default_factory=list)


class _LedgerChatWrites:
    """记账器用的聊天仓储：写库原样交给真实仓储，另记下每句主播台词写进了哪一场。"""

    def __init__(self, chat: ChatRepo) -> None:
        self._chat = chat
        self.assistant_sessions: List[int] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._chat, name)

    async def insert_live_chat(self, **fields: Any) -> Any:
        result = await self._chat.insert_live_chat(**fields)
        if fields.get("sender_role") == "assistant":
            self.assistant_sessions.append(int(fields["live_session_id"]))
        return result


@pytest.fixture
async def live_stack(store: SQLiteDatabase) -> AsyncIterator[_LiveStack]:
    """续播链路：场次管理器发 live.*，拦截器给事件补场次号，记账器把台词写进 live_chat。

    管理器不在 fixture 里启动：残留场次要先把行插进库，再 ``manager.start()``
    （进程启动期）检出待决——真实时序是"崩溃留下行 → 重启检出"。
    """
    bus = EventBus()
    manager = LiveSessionManager(store.sessions, store.chat, bus, platform="bilibili", room_id="room")
    bus.add_interceptor(SessionStampInterceptor(manager))
    writes = _LedgerChatWrites(store.chat)
    ledger = StorageLedger(bus, writes, store.viewers, store.events, session_manager=manager)  # type: ignore[arg-type]
    await ledger.start()
    stack = _LiveStack(store=store, bus=bus, manager=manager, writes=writes)

    async def _on_speech(event_name: str, payload: StreamerSpeechPayload, source: str) -> None:
        stack.speeches.append(payload)

    bus.on(CoreEvents.STREAMER_SPEECH, _on_speech, model_class=StreamerSpeechPayload)
    try:
        yield stack
    finally:
        await ledger.stop()
        await bus.cleanup()


def _routed_llm(planner_requests: List[str]) -> MagicMock:
    """Planner 决定直接开口（接回发言），表达侧固定回续播句；planner 请求文本按序收集。"""
    llm = MagicMock()

    async def _generate(*args: Any, **kwargs: Any) -> Response:
        if kwargs.get("profile") == "planner":
            planner_requests.append(str(args[0]))
            return Response(
                success=True,
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_reply",
                        name="streamer_reply",
                        arguments={"topic_summary": "断线续播", "reply_guidance": "接回直播", "is_novel": True},
                    )
                ],
            )
        return Response(success=True, content=RESUME_LINE)

    llm.generate = AsyncMock(side_effect=_generate)
    return llm


async def _interrupted_session_with_chat(store: SQLiteDatabase, kind: str = "live") -> int:
    """一场崩溃时留下的进行中场次（未结账行 + 断前的观众弹幕）。"""
    pk = await store.sessions.insert_live_session(
        stream_id="room",
        platform="bilibili",
        started_at_ms=1_000,
        kind=kind,  # type: ignore[arg-type]
    )
    await store.chat.insert_live_chat(
        live_session_id=pk,
        timestamp_ms=2_000,
        sender_role="viewer",
        sender_id="uid_ajie",
        sender_name="阿杰",
        content="刚才说到TNT阵还差最后一排",
        message_type="danmaku",
        platform="bilibili",
        message_id="m_before_crash",
    )
    return pk


async def _started_agent(stack: _LiveStack, llm: Any, **kwargs: Any) -> StreamerAgent:
    agent = make_streamer_agent(
        {"background": {"enabled": False}, "mouth": {"enabled": False}},
        llm=llm,
        prompt=stub_prompt(),
        event_bus=stack.bus,
        tool_registry=ToolRegistry(),
        chat_repo=stack.store.chat,
        session_manager=stack.manager,
        **kwargs,
    )
    await agent.start()
    return agent


async def test_resume_round_reads_interrupted_history_and_lands_in_same_session(
    live_stack: _LiveStack,
) -> None:
    """续播：接回发言那一轮看得到断前的对话原文（历史按场次主键接上），接回句落进续播的那一场。"""
    planner_requests: List[str] = []
    llm = _routed_llm(planner_requests)
    stack = live_stack
    pk = await _interrupted_session_with_chat(stack.store)
    await stack.manager.start()  # 进程启动：检出残留场次为待决
    agent = await _started_agent(stack, llm)
    try:
        await stack.manager.resume_session(pk)
        await wait_until(lambda: len(stack.speeches) >= 1, what="接回发言的发言事件送达")
        # 记账器与本测试的发言订阅并发处理同一条 streamer.speech：本测试的订阅只是记一笔，
        # 记账器还要异步写库，看到发言事件时那一行未必已落库。先等写库落地再断言写进了哪一场
        await wait_until(lambda: len(stack.writes.assistant_sessions) >= 1, what="记账器把接回句写库")
    finally:
        await agent.stop()

    assert agent.is_live_active(), "续播场接回后主动发言放行"
    assert planner_requests, "接回发言窗跑了一轮决策"
    assert "刚才说到TNT阵还差最后一排" in planner_requests[0], "断前的对话原文进决策上下文（历史接上）"
    assert "断线续播" in planner_requests[0], "接回窗带 RESUME 情境标注"
    assert [speech.live_session_id for speech in stack.speeches] == [pk], "接回句带的是续播场的编号"
    assert stack.writes.assistant_sessions == [pk], "记账器把接回句写进续播场"
    row = await stack.store.sessions.get_live_session(live_session_id=pk)
    assert row is not None and row.ended_at_ms is None, "续播不写结束时间"


@pytest.mark.parametrize("kind", ["live", "rehearsal"])
async def test_resume_restores_only_my_unfinished_delegation_of_this_session(live_stack: _LiveStack, kind: str) -> None:
    """续播认领（正式场与彩排场一样）：这一场重启前我派的、没交付完的那件记回手头的事，写着被打断了、
    做到哪不确定；运营直派的调试指令、别的场次的委派、已经交付的都不记回来。主播侧只读委派账，不写库。"""
    planner_requests: List[str] = []
    stack = live_stack
    earlier = await stack.store.sessions.insert_live_session(stream_id="room", platform="bilibili", started_at_ms=500)
    await stack.store.sessions.close_live_session(live_session_id=earlier, ended_at_ms=900)
    pk = await _interrupted_session_with_chat(stack.store, kind=kind)
    rows = (
        (pk, "minecraft", "deleg_done", "accepted", "搭个棚子", 1_100, PLANNER_CALL_SOURCE),
        (pk, "minecraft", "deleg_done", "succeeded", "delivery: 棚子搭好了", 1_200, PLANNER_CALL_SOURCE),
        (pk, "minecraft", "deleg_open", "accepted", "把营地的门装好", 1_500, PLANNER_CALL_SOURCE),
        (pk, "text_adv", "deleg_debug", "accepted", "运营调试：读一遍存档", 1_600, "operator"),
        (earlier, "game_2048", "deleg_old", "accepted", "上一场没玩完的 2048", 600, PLANNER_CALL_SOURCE),
    )
    for sid, game, task_id, kind_, text, ts, initiator in rows:
        await stack.store.events.insert_delegation_event(
            live_session_id=sid, game=game, task_id=task_id, kind=kind_, text=text, timestamp_ms=ts, initiator=initiator
        )
    await stack.manager.start()  # 进程启动：检出残留场次为待决
    agent = await _started_agent(stack, _routed_llm(planner_requests), event_repo=stack.store.events)
    try:
        await stack.manager.resume_session(pk)
        await wait_until(lambda: len(planner_requests) >= 1, what="接回那一轮决策")
    finally:
        await agent.stop()

    work = planner_requests[0].split("【我手头在游戏里做的事】", 1)[1].split("【", 1)[0]
    assert "开始做一个任务：把营地的门装好" in work and "重启前在做这件事，被打断了，做到哪不确定" in work
    assert "运营调试" not in work and "2048" not in work and "搭个棚子" not in work
    statuses = {r.task_id: r.status for r in await stack.store.events.list_session_delegations(live_session_id=pk)}
    assert statuses == {"deleg_done": "succeeded", "deleg_open": "accepted", "deleg_debug": "accepted"}, (
        "主播侧不写委派账：被打断的终态由存储落库器在启动时记"
    )


async def test_plain_open_session_schedules_no_resume_round(live_stack: _LiveStack) -> None:
    """普通开播不排接回轮：open_session 顶掉已续播的场后，只有续播那一轮 RESUME 决策。"""
    executor = FakeRoundExecutor()
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=True, content=""))
    stack = live_stack
    pk = await _interrupted_session_with_chat(stack.store)
    await stack.manager.start()  # 进程启动：检出残留场次为待决
    agent = make_streamer_agent(
        {"background": {"enabled": False}, "mouth": {"enabled": False}},
        llm=llm,
        prompt=stub_prompt(),
        event_bus=stack.bus,
        tool_registry=ToolRegistry(),
        chat_repo=stack.store.chat,
        session_manager=stack.manager,
        round_executor=executor,
    )
    await agent.start()
    try:
        await stack.manager.resume_session(pk)
        await wait_until(lambda: len(executor.calls) >= 1, what="续播接回轮执行")

        await stack.manager.open_session(kind="live", title="新场")
        await wait_until(lambda: stack.manager.active_pk != pk, what="新场顶掉续播场")
    finally:
        await agent.stop()

    assert len(executor.calls) == 1, "普通开播不排接回轮"
    assert executor.calls[0]["trigger_cause"].kind.value == "resume"
    assert executor.calls[0]["trigger_cause"].source.value == "live_start"
    assert executor.calls[0]["forced"] is True and executor.calls[0]["proactive"] is True


async def test_resume_event_carries_resumed_flag(store: SQLiteDatabase, event_bus: EventBus) -> None:
    """live.started 的 resumed 标记只在续播广播为 True，普通开播为 False。"""
    manager = LiveSessionManager(store.sessions, store.chat, event_bus, platform="bilibili")
    started: List[LiveStartedPayload] = []

    async def _on_started(event_name: str, payload: LiveStartedPayload, source: str) -> None:
        started.append(payload)

    event_bus.on(CoreEvents.LIVE_STARTED, _on_started, model_class=LiveStartedPayload)
    pk = await _interrupted_session_with_chat(store)
    await manager.start()  # 先插残留行再启动：启动期检出待决

    await manager.resume_session(pk)
    await manager.open_session(kind="live")
    await event_bus.wait_until_drained()

    assert [payload.resumed for payload in started] == [True, False]
    assert started[0].live_session_id == pk
