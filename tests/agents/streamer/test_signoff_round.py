"""收播道别窗与队尾：道别那一轮看得见、落得进正在结束的那一场；下播整理与停机前先等队尾播完。

道别窗的回归用真实的场次管理器、场次盖章拦截器、记账器与 SQLite 临时库串起来：
手动下播时场次先写成已结束、再发 ``live.ended``，道别那一轮必须按正在结束的那一场
读历史、给道别句盖那一场的编号、按那一场的类型回想；这一场只在道别那一轮自己的调用链里
看得见，道别期间新开的场次与并发的常规轮次照常按进行中场次走。场次行已删或将删（空场次、
删除进行中场次）时道别照常跑，但道别句不落库、不排收播整理。

队尾的用例用一个由测试放行的假 TTS 引擎卡住播放，观察"播完才整理、播完才停"；
TTS 卡死的情形把播放队列的空闲等待（公开方法）换成立即报"没等到"的替身，
不真的等满 30 秒。

道别窗用例不用 ``EventBus.wait_until_drained`` 等派发落地：它在 Python 3.12 下遇到
"任务已完成、移出回调还没轮到"的时刻会同步空转卡死整个事件循环（已另行上报），
这里改等本轮决策事件、发言事件与记账器的写库记录（共享等待助手 ``wait_until``）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.planner import RoundOutcome
from src.agents.streamer.streamer_agent import StreamerAgent
from src.agents.streamer.utterance_queue import UtteranceQueue
from src.modules.events.event_bus import EventBus
from src.modules.events.interceptors.session_stamp import SessionStampInterceptor
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload
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
from tests.modules.llm.fakes import FakeLLMManager

# 登记（驱动/只读穿透，合并时进底账）：队尾用例与"场次行已删不排整理"用例直接驱动下播回调
# （agent._on_live_ended），并等后台整理任务收尾（agent._memory_tasks）；二者均无公开观测面。

FAREWELL = "今天就到这儿啦，下次直播继续炸TNT！"
GREETING = "晚上好呀，正式场开始啦！"
_TAIL_WAIT_MS = 30_000
_TTS_ON = {"enabled": True, "max_queue": 3, "render_timeout_ms": 60_000, "wait_playback": False}


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


def _recall_call(query: str = "钻石") -> ToolCall:
    """回想工具（阶段 3b 起由 memory_recall 取代旧的两个回想工具）。"""
    return ToolCall(id="call_recall", name="memory_recall", arguments={"query": query, "scope": "all"})


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
    stack: _LiveStack, llm: MagicMock, prompt: Optional[MagicMock] = None, *, with_sessions: bool = False
) -> StreamerAgent:
    """真实 Planner + Replyer；只接聊天仓储与回顾、印象两张表（回想工具要用），不起收播整理。

    ``with_sessions`` 再接上场次表：备忘录每次写入落进场次行，用例才看得到哪一场存了什么；
    同时收播整理也会起（下播后后台用 summary 档位写一次回顾）。默认不接——接上后开播时会把
    上场回顾读进系统提示词，按整份请求断言的用例会被它干扰。
    """
    agent = make_streamer_agent(
        {"background": {"enabled": False}, "mouth": {"enabled": False}},
        llm=llm,
        prompt=prompt,
        event_bus=stack.bus,
        tool_registry=ToolRegistry(),
        chat_repo=stack.store.chat,
        sessions_repo=stack.store.sessions if with_sessions else None,
        session_manager=stack.manager,
        # 下播整理走账本（承诺与场次档案）：接上仓储才会在道别后起一次 summary 档位整理
        commitments_repo=stack.store.commitments,
        archive_repo=stack.store.archive,
    )
    await agent.start()
    return agent


async def _past_session_with_recap(store: SQLiteDatabase, *, kind: SessionKind, recap: str) -> None:
    """造一场已整理过的同类型场次：常驻简报取它的档案行（阶段 3b 起不再注入旧回顾文本）。"""
    pk = await store.sessions.insert_live_session(stream_id="room", platform="bilibili", started_at_ms=1_000, kind=kind)
    await store.sessions.close_live_session(live_session_id=pk, ended_at_ms=9_000)
    await store.archive.insert_line(live_session_id=pk, text=recap, quote=recap, evidence_chat_id=1)


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
    # 道别那一轮只查属于它那一类场次的档案；下播后的轮次没有进行中场次，按正式场查
    signoff_query = "新皮肤" if "彩排" in recalled else "钻石"
    llm = _routed_llm(
        [
            _planner_step(_recall_call(signoff_query)),
            _planner_step(_reply_call()),
            _planner_step(_recall_call("钻石")),
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
    # 道别那一轮跑完之后：常驻简报仍是这一场开播时定的那份（整场不变，不会因为下播就换一份），
    # 回想工具不按场次类型过滤，往场的档案照样查得到
    assert "正式那场挖到了钻石" in after_recall, "道别那一轮跑完，回想工具照样能翻到往场的档案"


async def test_signoff_round_recalls_this_stream_of_the_ending_session(live_stack: _LiveStack) -> None:
    """道别那一轮回想"本场"：查的是正在结束的那一场，原文不遮名。

    场次管理器发 live.ended 前已清掉进行中场次；回想工具取"本场"若照常读进行中场次，
    道别时会答"现在不在直播"，或把刚结束的这一场当成往场、把观众昵称遮掉。
    """
    store, manager = live_stack.store, live_stack.manager
    llm = _routed_llm(
        [
            _planner_step(
                ToolCall(
                    id="call_recall_stream",
                    name="memory_recall",
                    arguments={"query": "TNT阵", "scope": "this_stream"},
                )
            ),
            _planner_step(_reply_call()),
        ]
    )
    agent = await _started_signoff_agent(live_stack, llm)
    try:
        pk = await manager.open_session(kind="live", title="炸TNT")
        await wait_until(agent.is_live_active, what="开播回调跑完")
        await _viewer_says(store, pk, "小鱼", "下播前把TNT阵炸了吧")
        await _viewer_says(store, pk, "阿杰", "TNT阵等小鱼来了再炸")

        await _end_and_wait_signoff(live_stack, agent)
    finally:
        await agent.stop()

    _signoff_plan, after_recall = _planner_requests(llm)
    assert "现在不在直播" not in after_recall, "道别那一轮还算在这一场里"
    assert "TNT阵等小鱼来了再炸" in after_recall, "本场的原文不遮观众昵称"


async def test_signoff_during_new_session_does_not_leak_into_concurrent_round(live_stack: _LiveStack) -> None:
    """彩排场手动下播，道别那一轮还在等模型时开了正式场、跑了一轮常规决策：这一轮按正式场
    读历史、按正式场回想上一场、台词记进正式场；道别那一轮照旧按彩排场读、记进彩排场。"""
    store, manager = live_stack.store, live_stack.manager
    await _past_session_with_recap(store, kind="live", recap="正式那场挖到了钻石")
    await _past_session_with_recap(store, kind="rehearsal", recap="彩排那场试了新皮肤")
    gate, entered = asyncio.Event(), asyncio.Event()
    # 并发的常规轮次：先回想上一场，再开口打招呼
    regular_steps = [
        _planner_step(_recall_call("钻石")),
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


class _Gate:
    """Planner 脚本里的闸：走到这一步先报"已进闸"，等测试放行后才交出这一步的回复。"""

    def __init__(self, response: Response) -> None:
        self.response = response
        self.entered = asyncio.Event()
        self.released = asyncio.Event()


def _scripted_llm(planner_steps: List[Any]) -> MagicMock:
    """Planner 按调用先后逐步取脚本，遇到闸就停在闸前等放行；表达侧固定回道别句。"""
    llm = MagicMock()
    steps = list(planner_steps)

    async def _generate(*args: Any, **kwargs: Any) -> Response:
        if kwargs.get("profile") != "planner":
            return Response(success=True, content=FAREWELL)
        step = steps.pop(0)
        if isinstance(step, _Gate):
            step.entered.set()
            await step.released.wait()
            return step.response
        return step

    llm.generate = AsyncMock(side_effect=_generate)
    return llm


async def test_round_opened_before_live_end_stays_silent_after_signoff(live_stack: _LiveStack) -> None:
    """常规轮开窗后、说完前运营结束了场次：道别那一轮照常说再见，这一轮的台词作废不出声，
    也不记进这一场——道别句就是本场最后一句，下播整理不会把下播后冒出来的话写进档案。"""
    store, manager = live_stack.store, live_stack.manager
    held = _Gate(_planner_step(_reply_call(topic="炉子还没点火", guidance="吐槽自己拖延")))
    llm = _scripted_llm([held, _planner_step(_reply_call())])
    agent = await _started_signoff_agent(live_stack, llm)
    try:
        live = await manager.open_session(kind="live", title="正式开播")
        await wait_until(agent.is_live_active, what="开播回调跑完")
        # 场次里得有消息：空场次在结账时整行丢弃，道别句就不落库了
        await _viewer_says(store, live, "小鱼", "炉子点了吗")
        regular = asyncio.create_task(agent.debug_test_decision(batch=[{"nickname": "小鱼", "text": "炉子点了吗"}]))
        await asyncio.wait_for(held.entered.wait(), timeout=10)  # 常规轮停在 Planner 等放行

        await _end_and_wait_signoff(live_stack, agent)
        held.released.set()
        result = await asyncio.wait_for(regular, timeout=10)
        lines = await _stored_assistant_lines(live_stack, live, expect=1)
    finally:
        await agent.stop()

    assert result["speech"] is None and result["silent_reason"] == "session_ended"
    assert [speech.text for speech in live_stack.speeches] == [FAREWELL]
    assert [row.content for row in lines] == [FAREWELL]


def _watch_idle_waits(monkeypatch: pytest.MonkeyPatch, *, stuck: bool = False) -> _IdleWaits:
    """把播放队列的空闲等待（公开方法）换成记录版；``stuck`` 模拟 TTS 卡死：立即报"等满上限仍没播完"。

    发言等播放与嘴都关着，队尾等待是这里唯一的调用方。
    """
    original = UtteranceQueue.wait_idle
    waits = _IdleWaits()

    async def _watched(self: UtteranceQueue, timeout_ms: Optional[int] = None) -> bool:
        waits.timeouts.append(timeout_ms)
        waits.entered.set()
        if stuck:
            return False
        return await original(self, timeout_ms=timeout_ms)

    monkeypatch.setattr(UtteranceQueue, "wait_idle", _watched)
    return waits


def _farewell_planner() -> MagicMock:
    """每轮都决定开口说道别句的 Planner 替身（发言照常走真实的决策执行器与发声管线）。"""
    planner = MagicMock()
    planner.plan = AsyncMock(
        return_value=RoundOutcome(
            replied=True,
            topic_summary="收播道别",
            reply_guidance="说再见",
            reply_payload={"speech": FAREWELL, "emotion": {"name": "happy", "intensity": 0.6}},
        )
    )
    return planner


def _tail_agent(engine: _GatedEngine, llm: Any = None, store: Optional[SQLiteDatabase] = None) -> StreamerAgent:
    """TTS 开着的主播；给了库就接上场次回顾整理（只开回顾这一步，用模型调用观察整理何时开始）。"""
    repos = (
        {
            "chat_repo": store.chat,
            "sessions_repo": store.sessions,
            "commitments_repo": store.commitments,
            "archive_repo": store.archive,
        }
        if store is not None
        else {}
    )
    return make_streamer_agent(
        {"background": {"enabled": False}, "mouth": {"enabled": False}},
        llm=llm,
        planner=_farewell_planner(),
        tts_engine=engine,
        speech_config=_TTS_ON,
        **repos,
    )


async def _ended_session_with_lines(store: SQLiteDatabase) -> int:
    """一场已结束、有对话可整理的场次（场次管理器写成已结束之后才发 live.ended）。"""
    pk = await store.sessions.insert_live_session(stream_id="room", platform="bilibili", started_at_ms=1_000)
    await _viewer_says(store, pk, "阿杰", "TNT阵还差最后一排没铺完")
    # 回顾以她自己说过的话为原料：至少留一句主播台词，整理才会调模型
    await store.chat.insert_live_chat(
        live_session_id=pk,
        timestamp_ms=now_ms(),
        sender_role="assistant",
        sender_name="主播",
        content="最后一排明天再铺",
        message_type="speak",
    )
    await store.sessions.close_live_session(live_session_id=pk, ended_at_ms=now_ms())
    return pk


async def _started_with_idle_bootstrap(agent: StreamerAgent) -> None:
    await agent.start()
    await asyncio.gather(*agent._memory_tasks)  # 启动补写无场次可补，空跑结束


class _GatedEngine:
    """TTS 引擎替身：每句停在播放中，测试放行才播完；播放中被取消（停队列）的记为没播完。"""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finished: List[str] = []
        self.cancelled: List[str] = []

    async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.append(text)
            raise
        self.finished.append(text)


@dataclass
class _IdleWaits:
    """播放队列空闲等待的记录：每次要等多久，以及有没有人开始等。"""

    timeouts: List[Optional[int]] = field(default_factory=list)
    entered: asyncio.Event = field(default_factory=asyncio.Event)


async def test_settle_starts_after_signoff_line_finishes_playing(
    store: SQLiteDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """下播：道别句排在队尾播放中，整理排进后台先等它播完（上限 30 秒），播完才调模型整理。"""
    engine, llm = _GatedEngine(), FakeLLMManager()
    agent = _tail_agent(engine, llm, store)
    await _started_with_idle_bootstrap(agent)
    waits = _watch_idle_waits(monkeypatch)
    pk = await _ended_session_with_lines(store)
    try:
        await agent._on_live_ended("live.ended", LiveEndedPayload(live_session_id=pk), "test")
        await asyncio.wait_for(engine.started.wait(), timeout=5)
        await asyncio.wait_for(waits.entered.wait(), timeout=5)
        assert llm.calls == [], "道别句还在播，整理不开始"

        engine.release.set()
        await asyncio.gather(*agent._memory_tasks)
    finally:
        await agent.stop()

    assert engine.finished == [FAREWELL] and engine.cancelled == []
    assert llm.calls, "道别句播完后才开始整理"
    assert waits.timeouts[0] == _TAIL_WAIT_MS


async def test_stop_waits_for_queued_line_before_stopping_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """关程序：队里还有她刚说的一句，停发声管线前先等它播完，不被当没送达丢掉。"""
    engine = _GatedEngine()
    agent = _tail_agent(engine)
    await agent.start()
    waits = _watch_idle_waits(monkeypatch)
    await agent.debug_test_decision(batch=[{"nickname": "阿杰", "text": "主播晚安"}])
    await asyncio.wait_for(engine.started.wait(), timeout=5)

    stopping = asyncio.create_task(agent.stop())
    await asyncio.wait_for(waits.entered.wait(), timeout=5)
    assert not stopping.done() and engine.cancelled == [], "还在播，停机等着"

    engine.release.set()
    await stopping

    assert engine.finished == [FAREWELL] and engine.cancelled == []
    assert waits.timeouts == [_TAIL_WAIT_MS]


async def test_stuck_tts_waits_up_to_limit_then_settles_and_stops_anyway(
    store: SQLiteDatabase, monkeypatch: pytest.MonkeyPatch, loguru_capture: Any
) -> None:
    """TTS 卡死：下播整理与停机各等满 30 秒上限后照常进行，卡住的那句随队列停止作废。"""
    engine, llm = _GatedEngine(), FakeLLMManager()
    agent = _tail_agent(engine, llm, store)
    await _started_with_idle_bootstrap(agent)
    waits = _watch_idle_waits(monkeypatch, stuck=True)
    pk = await _ended_session_with_lines(store)

    await agent._on_live_ended("live.ended", LiveEndedPayload(live_session_id=pk), "test")
    await asyncio.wait_for(engine.started.wait(), timeout=5)
    await asyncio.gather(*agent._memory_tasks)
    assert llm.calls, "等满上限仍没播完：照常整理"
    assert engine.finished == []

    await agent.stop()

    assert waits.timeouts == [_TAIL_WAIT_MS, _TAIL_WAIT_MS], "下播整理与停机各等一次，上限都是 30 秒"
    assert engine.cancelled == [FAREWELL], "卡住的那句随队列停止作废"
    stuck_warnings = [r for r in loguru_capture.records if r["level"] == "WARNING" and "仍没播完" in r["message"]]
    assert len(stuck_warnings) == 2


# ---------------------------------------------------------------------------
# 场次行已删或将删：道别照常跑，不排收播整理
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "row_gone",
    [{"deleted": True}, {"empty_discarded": True}],
    ids=["场次行将被删除", "空场次已丢弃"],
)
async def test_signoff_schedules_no_settle_when_session_row_is_gone(
    store: SQLiteDatabase, row_gone: Dict[str, bool]
) -> None:
    """live.ended 标着场次行已删或将删：道别照常跑，但不排收播整理，道别句也不落库。

    这一场的行还在、有可整理的对话（模拟删除场次时 live.ended 先到、删行在后）：整理一旦
    排上就会调模型写回顾，所以"没有模型调用"就是"没排整理"。两种情形下道别那一轮都不交
    场次上下文，道别句不带场次编号；记账器没有可归属的场次时跳过写入——库里不留这一场的
    孤儿行，也不会写出一行主播放台词。
    """
    llm, planner = FakeLLMManager(), _farewell_planner()
    bus = EventBus()
    writes = _LedgerChatWrites(store.chat)
    ledger = StorageLedger(bus, writes, store.viewers, store.events)  # type: ignore[arg-type]
    await ledger.start()
    speeches: List[StreamerSpeechPayload] = []

    async def _on_speech(event_name: str, payload: StreamerSpeechPayload, source: str) -> None:
        speeches.append(payload)

    bus.on(CoreEvents.STREAMER_SPEECH, _on_speech, model_class=StreamerSpeechPayload)
    agent = make_streamer_agent(
        {"background": {"enabled": False}, "mouth": {"enabled": False}},
        llm=llm,
        planner=planner,
        event_bus=bus,
        chat_repo=store.chat,
        sessions_repo=store.sessions,
        commitments_repo=store.commitments,
        archive_repo=store.archive,
    )
    await _started_with_idle_bootstrap(agent)
    pk = await _ended_session_with_lines(store)
    try:
        await agent._on_live_ended("live.ended", LiveEndedPayload(live_session_id=pk, **row_gone), "test")
        await asyncio.gather(*agent._memory_tasks)
        await wait_until(lambda: len(speeches) == 1, what="道别句的发言事件送达")
    finally:
        await agent.stop()
        await ledger.stop()

    planner.plan.assert_awaited_once()
    assert llm.calls == [], "场次行已删或将删：不排收播整理，不给这一场写回顾"
    assert [speech.live_session_id for speech in speeches] == [0], "不给道别句盖已删场次的编号"
    assert writes.assistant_sessions == [], "记账器不写主播放台词行"
    stored = await store.chat.list_recent_live_chat(live_session_id=pk, limit=None, sender_role="assistant")
    assert [row.content for row in stored] == ["最后一排明天再铺"], "道别句不落库：库里只留这一场原有的台词"
    orphan_rows = await store.execute("SELECT COUNT(*) AS n FROM live_chat WHERE content = ?", (FAREWELL,))
    assert int(orphan_rows[0]["n"]) == 0, "库里没有任何一行是这句道别"
