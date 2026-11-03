"""收播道别窗：道别那一轮看得见、落得进正在结束的那一场。

道别窗的回归用真实的场次管理器、场次盖章拦截器、记账器与 SQLite 临时库串起来：
手动下播时场次先写成已结束、再发 ``live.ended``，道别那一轮必须按正在结束的那一场
读历史、给道别句盖那一场的编号、按那一场的类型回想；这一场只在道别那一轮自己的调用链里
看得见，道别期间新开的场次与并发的常规轮次照常按进行中场次走。场次行已删（空场次）时
道别照常跑，但道别句不落库。

道别窗用例不用 ``EventBus.wait_until_drained`` 等派发落地：它在 Python 3.12 下遇到
"任务已完成、移出回调还没轮到"的时刻会同步空转卡死整个事件循环（已另行上报），
这里改等本轮决策事件、发言事件与记账器的写库记录（共享等待助手 ``wait_until``）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.events.event_bus import EventBus
from src.modules.events.interceptors.session_stamp import SessionStampInterceptor
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.planner import PlannerDecisionPayload
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.llm.payload import Response, ToolCall
from src.modules.session.manager import LiveSessionManager
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.chat import LiveChatMessage
from src.modules.storage.repos import ChatRepo
from src.modules.storage.storage_ledger import StorageLedger
from src.modules.tools import ToolRegistry
from src.modules.types.session_kind import SessionKind
from src.modules.utils.time_utils import now_ms
from tests.agents.streamer.helpers import make_streamer_agent, stub_prompt
from tests.config.helpers import wait_until

FAREWELL = "今天就到这儿啦，下次直播继续炸TNT！"
GREETING = "晚上好呀，正式场开始啦！"


# ---------------------------------------------------------------------------
# 道别窗：真实场次管理器 + 盖章拦截器 + 记账器
# ---------------------------------------------------------------------------


class _LedgerChatWrites:
    """记账器用的聊天仓储：写库原样交给真实仓储，另记下每句主播台词写进了哪一场。

    记账器在自己的订阅任务里写库，测试拿不到那个任务；包这一层只为知道"这句已经写完"，
    好用共享等待助手等它，写库本身仍走真实仓储。其余方法原样转给真实仓储。
    """

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


@dataclass
class _LiveStack:
    store: SQLiteDatabase
    bus: EventBus
    manager: LiveSessionManager
    writes: _LedgerChatWrites
    speeches: List[StreamerSpeechPayload] = field(default_factory=list)
    decided: asyncio.Event = field(default_factory=asyncio.Event)


@pytest.fixture
async def live_stack(store: SQLiteDatabase) -> AsyncIterator[_LiveStack]:
    """手动下播链路：场次管理器发 live.*，拦截器给事件补场次号，记账器把台词写进 live_chat。"""
    bus = EventBus()
    manager = LiveSessionManager(store.sessions, store.chat, bus, platform="bilibili", room_id="room")
    await manager.start()
    bus.add_interceptor(SessionStampInterceptor(manager))
    writes = _LedgerChatWrites(store.chat)
    ledger = StorageLedger(bus, writes, store.viewers, store.events, session_manager=manager)  # type: ignore[arg-type]
    await ledger.start()
    stack = _LiveStack(store=store, bus=bus, manager=manager, writes=writes)

    async def _on_speech(event_name: str, payload: StreamerSpeechPayload, source: str) -> None:
        stack.speeches.append(payload)

    async def _on_decision(event_name: str, payload: PlannerDecisionPayload, source: str) -> None:
        stack.decided.set()

    bus.on(CoreEvents.STREAMER_SPEECH, _on_speech, model_class=StreamerSpeechPayload)
    bus.on(CoreEvents.PLANNER_DECISION, _on_decision, model_class=PlannerDecisionPayload)
    try:
        yield stack
    finally:
        await ledger.stop()
        await bus.cleanup()


def _planner_step(*calls: ToolCall) -> Response:
    return Response(success=True, content="", tool_calls=list(calls))


def _reply_call(topic: str = "收播道别", guidance: str = "和大家说再见，约好下次接着炸") -> ToolCall:
    args = {"topic_summary": topic, "reply_guidance": guidance, "is_novel": True}
    return ToolCall(id="call_reply", name="streamer_reply", arguments=args)


def _no_more_calls() -> Response:
    """Planner 不再调工具：这一轮到此为止，不开口。"""
    return Response(success=True, content="")


def _recall_call() -> ToolCall:
    return ToolCall(id="call_recall", name="memory_recall_session", arguments={"n_back": 1})


def _routed_llm(planner_steps: List[Response]) -> MagicMock:
    """按档位分流：Planner 依次取预置步骤，表达侧固定回道别句。"""
    llm = MagicMock()

    async def _generate(*args: Any, **kwargs: Any) -> Response:
        if kwargs.get("profile") == "planner":
            return planner_steps.pop(0)
        return Response(success=True, content=FAREWELL)

    llm.generate = AsyncMock(side_effect=_generate)
    return llm


def _gated_signoff_llm(gate: asyncio.Event, entered: asyncio.Event, later_steps: List[Response]) -> MagicMock:
    """道别那一轮的 Planner（第一次 Planner 调用）停在闸前等测试放行，放行后决定开口道别；
    之后的 Planner 调用（道别期间并发的常规轮次）依次取预置步骤。表达侧放行前回打招呼的话、
    放行后回道别句，两轮的台词分得开。"""
    llm = MagicMock()
    planner_calls = 0

    async def _generate(*args: Any, **kwargs: Any) -> Response:
        nonlocal planner_calls
        if kwargs.get("profile") != "planner":
            return Response(success=True, content=FAREWELL if gate.is_set() else GREETING)
        planner_calls += 1
        if planner_calls == 1:
            entered.set()
            await gate.wait()
            return _planner_step(_reply_call())
        return later_steps.pop(0)

    llm.generate = AsyncMock(side_effect=_generate)
    return llm


def _planner_requests(llm: MagicMock) -> List[str]:
    return [str(call.args[0]) for call in llm.generate.await_args_list if call.kwargs.get("profile") == "planner"]


def _replyer_dialogues(prompt: MagicMock) -> List[str]:
    """表达侧每次渲染本轮输入时交进去的聊天记录（按渲染先后；只有表达侧模板带这一项）。"""
    return [
        str(call.kwargs["dialogue_prompt"]) for call in prompt.render.call_args_list if "dialogue_prompt" in call.kwargs
    ]


async def _started_signoff_agent(
    stack: _LiveStack, llm: MagicMock, prompt: Optional[MagicMock] = None
) -> StreamerAgent:
    """真实 Planner + Replyer；只接聊天仓储与回顾、印象两张表（回想工具要用），不起收播整理。"""
    agent = make_streamer_agent(
        {"background": {"enabled": False}, "mouth": {"enabled": False}},
        llm=llm,
        prompt=prompt,
        event_bus=stack.bus,
        tool_registry=ToolRegistry(),
        chat_repo=stack.store.chat,
        session_manager=stack.manager,
        recap_repo=stack.store.recaps,
        viewer_profile_repo=stack.store.viewer_profiles,
    )
    await agent.start()
    return agent


async def _past_session_with_recap(store: SQLiteDatabase, *, kind: SessionKind, recap: str) -> None:
    pk = await store.sessions.insert_live_session(stream_id="room", platform="bilibili", started_at_ms=1_000, kind=kind)
    await store.sessions.close_live_session(live_session_id=pk, ended_at_ms=9_000)
    await store.recaps.upsert_recap(live_session_id=pk, started_at_ms=1_000, ended_at_ms=9_000, recap_text=recap)


async def _viewer_says(store: SQLiteDatabase, pk: int, name: str, text: str) -> None:
    await store.chat.insert_live_chat(
        live_session_id=pk,
        timestamp_ms=now_ms(),
        sender_role="viewer",
        sender_id=f"uid_{name}",
        sender_name=name,
        content=text,
        message_type="danmaku",
        platform="bilibili",
        message_id=f"m_{name}_{text}",
    )


async def _stored_assistant_lines(stack: _LiveStack, pk: int, *, expect: int) -> List[LiveChatMessage]:
    """等记账器把 ``expect`` 句主播台词写进这一场，再从库里读出这一场的全部主播台词。"""
    await wait_until(
        lambda: stack.writes.assistant_sessions.count(pk) >= expect,
        what=f"记账器把 {expect} 句主播台词写进场次 {pk}",
    )
    return await stack.store.chat.list_recent_live_chat(live_session_id=pk, limit=None, sender_role="assistant")


async def _end_and_wait_signoff(
    stack: _LiveStack, agent: StreamerAgent, end: Optional[Callable[[], Awaitable[Any]]] = None
) -> None:
    """结束场次（缺省手动下播），等道别那一轮的决策事件发出（道别句先于它发出）。"""
    await (end() if end is not None else stack.manager.close_session(reason="手动结束"))
    await asyncio.wait_for(stack.decided.wait(), timeout=10)
    await wait_until(lambda: not agent.is_live_active(), what="下播回调收闸")


@pytest.mark.parametrize(
    ("kind", "recalled", "other_line"),
    [
        ("live", "正式那场挖到了钻石", "彩排那场试了新皮肤"),
        ("rehearsal", "彩排那场试了新皮肤", "正式那场挖到了钻石"),
    ],
)
async def test_signoff_round_reads_and_lands_in_the_ending_session(
    live_stack: _LiveStack, kind: SessionKind, recalled: str, other_line: str
) -> None:
    """手动下播：道别那一轮看得见本场最后几条原文，按本场类型回想，道别句落进刚结束的那一场；
    这一轮跑完，正在结束的场次随之失效，之后的轮次按"没开场次"回想（未开播按正式场算）。"""
    store, manager = live_stack.store, live_stack.manager
    await _past_session_with_recap(store, kind="live", recap="正式那场挖到了钻石")
    await _past_session_with_recap(store, kind="rehearsal", recap="彩排那场试了新皮肤")
    # 道别那一轮：先回想上一场，再开口道别；下播后的一轮调试闲聊：只回想上一场、不开口
    llm = _routed_llm(
        [
            _planner_step(_recall_call()),
            _planner_step(_reply_call()),
            _planner_step(_recall_call()),
            _no_more_calls(),
        ]
    )
    agent = await _started_signoff_agent(live_stack, llm)
    try:
        pk = await manager.open_session(kind=kind, title="炸TNT")
        await wait_until(agent.is_live_active, what="开播回调跑完")
        await _viewer_says(store, pk, "阿杰", "TNT阵还差最后一排没铺完")
        await _viewer_says(store, pk, "小鱼", "下次一定要把它炸了")

        await _end_and_wait_signoff(live_stack, agent)
        stored = await _stored_assistant_lines(live_stack, pk, expect=1)
        await agent.debug_test_decision(batch=[{"nickname": "阿杰", "text": "主播还在吗"}])
    finally:
        await agent.stop()

    first, second, _after_signoff, after_recall = _planner_requests(llm)
    assert "TNT阵还差最后一排没铺完" in first and "下次一定要把它炸了" in first, "道别那一轮看得到本场最后的原文"
    assert recalled in second and other_line not in second, "道别那一轮按正在结束的场次类型回想上一场"
    assert [speech.live_session_id for speech in live_stack.speeches] == [pk], "道别句带的是刚结束那一场的编号"
    assert [row.content for row in stored] == [FAREWELL], "记账器把道别句落进刚结束的那一场"
    # 彩排场那一组才分得出来：正在结束的场次若漏出道别那一轮（比如挂在 Agent 上没清掉），
    # 下播后的回想仍只翻彩排场
    assert "正式那场挖到了钻石" in after_recall and "彩排那场试了新皮肤" not in after_recall, (
        "道别那一轮跑完，正在结束的场次不再左右之后轮次的场次类型"
    )


async def test_signoff_during_new_session_does_not_leak_into_concurrent_round(live_stack: _LiveStack) -> None:
    """彩排场手动下播，道别那一轮还在等模型时开了正式场、跑了一轮常规决策：这一轮按正式场
    读历史、按正式场回想上一场、台词记进正式场；道别那一轮照旧按彩排场读、记进彩排场。"""
    store, manager = live_stack.store, live_stack.manager
    await _past_session_with_recap(store, kind="live", recap="正式那场挖到了钻石")
    await _past_session_with_recap(store, kind="rehearsal", recap="彩排那场试了新皮肤")
    gate, entered = asyncio.Event(), asyncio.Event()
    # 并发的常规轮次：先回想上一场，再开口打招呼
    regular_steps = [
        _planner_step(_recall_call()),
        _planner_step(_reply_call(topic="和观众打招呼", guidance="欢迎大家来看正式场")),
    ]
    llm, prompt = _gated_signoff_llm(gate, entered, regular_steps), stub_prompt()
    agent = await _started_signoff_agent(live_stack, llm, prompt)
    try:
        rehearsal = await manager.open_session(kind="rehearsal", title="彩排")
        await wait_until(agent.is_live_active, what="彩排场开播回调跑完")
        await _viewer_says(store, rehearsal, "阿杰", "彩排一下TNT阵")
        await manager.close_session(reason="彩排结束")
        await asyncio.wait_for(entered.wait(), timeout=10)  # 道别那一轮停在 Planner 等放行

        live = await manager.open_session(kind="live", title="正式开播")
        await wait_until(agent.is_live_active, what="正式场开播回调跑完")
        await _viewer_says(store, live, "小鱼", "正式场终于开了")
        await agent.debug_test_decision(batch=[{"nickname": "小鱼", "text": "主播晚上好"}])

        gate.set()
        await wait_until(lambda: len(live_stack.speeches) == 2, what="放行后道别那一轮说出道别句")
        rehearsal_lines = await _stored_assistant_lines(live_stack, rehearsal, expect=1)
        live_lines = await _stored_assistant_lines(live_stack, live, expect=1)
    finally:
        await agent.stop()

    signoff_plan, regular_plan, regular_recall = _planner_requests(llm)
    regular_reply, signoff_reply = _replyer_dialogues(prompt)
    assert "彩排一下TNT阵" in signoff_plan
    assert "正式场终于开了" in regular_plan and "彩排一下TNT阵" not in regular_plan
    assert "正式那场挖到了钻石" in regular_recall and "彩排那场试了新皮肤" not in regular_recall, (
        "并发的常规轮次按进行中的正式场取场次类型，不受道别那一轮影响"
    )
    assert "正式场终于开了" in regular_reply and "彩排一下TNT阵" not in regular_reply, (
        "并发的常规轮次表达侧读的是正式场的历史"
    )
    assert "彩排一下TNT阵" in signoff_reply and "正式场终于开了" not in signoff_reply, (
        "道别那一轮表达侧仍读正在结束的彩排场"
    )
    assert [(speech.text, speech.live_session_id) for speech in live_stack.speeches] == [
        (GREETING, live),
        (FAREWELL, rehearsal),
    ]
    assert [row.content for row in live_lines] == [GREETING]
    assert [row.content for row in rehearsal_lines] == [FAREWELL]


@pytest.mark.parametrize(
    ("viewer_lines", "how"),
    [([], "close")],
    ids=["空场次结账即丢弃"],
)
async def test_signoff_for_a_session_whose_row_is_gone_is_not_stored(
    live_stack: _LiveStack, viewer_lines: List[str], how: str
) -> None:
    """场次行已删：道别照常跑，道别句不带这一场的编号、不落库，库里不留孤儿行。

    空场次（开播后一条消息都没有）结账时整行丢弃，道别句要等模型才说出口，那时行早已删除。
    """
    store, manager = live_stack.store, live_stack.manager
    llm = _routed_llm([_planner_step(_reply_call())])
    agent = await _started_signoff_agent(live_stack, llm)
    try:
        pk = await manager.open_session(kind="live", title="要删的场")
        await wait_until(agent.is_live_active, what="开播回调跑完")
        for line in viewer_lines:
            await _viewer_says(store, pk, "阿杰", line)

        async def _delete() -> None:
            await manager.delete_session(pk)

        await _end_and_wait_signoff(live_stack, agent, _delete if how == "delete" else None)
        await wait_until(lambda: len(live_stack.speeches) == 1, what="道别句的发言事件送达")
    finally:
        await agent.stop()

    assert len(_planner_requests(llm)) == 1, "道别那一轮照常运行"
    # 编号 0 且没有进行中场次可补：记账器按"无法归属场次"跳过，不落库
    assert [speech.live_session_id for speech in live_stack.speeches] == [0], "不给道别句盖已删场次的编号"
    assert await store.sessions.get_live_session(live_session_id=pk) is None
    assert await store.chat.list_recent_live_chat(live_session_id=pk, limit=None) == [], "库里不留这一场的孤儿行"
    assert live_stack.writes.assistant_sessions == []
