"""主播 Agent 的记忆接线：账本仓储接上时才注册回想工具，开播拼常驻简报，点名用上即停推。

旧的场次回顾 / 长期记忆 / 观众画像已停写停注入，这里只测账本这条线。点名链路的用例用真实的
场次管理器、场次盖章拦截器、记账器与 SQLite 临时库串起来，等待落库用共享等待助手，不用
``EventBus.wait_until_drained``（原因见道别窗用例的模块说明）。
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any, AsyncGenerator, Generator, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.memory.cues import MemoryCueProvider
from src.agents.streamer.memory.render import format_memory_refs, memory_ref_numbers, render_commitment_number
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.events.event_bus import EventBus
from src.modules.events.interceptors.session_stamp import SessionStampInterceptor
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.llm.payload import Response, ToolCall
from src.modules.session.manager import LiveSessionManager
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.repos import ChatRepo
from src.modules.storage.storage_ledger import StorageLedger
from src.modules.tools import ToolRegistry
from src.modules.utils.time_utils import now_ms
from tests.agents.streamer.helpers import ActiveSessionStub, make_streamer_agent
from tests.config.helpers import wait_until
from tests.modules.llm.fakes import FakeLLMManager


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="streamer-memory-"))
    yield td / "memory.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    db = SQLiteDatabase(temp_db_path)
    await db.initialize()
    yield db
    await db.close()


def _prompts() -> Any:
    prompt_manager = MagicMock()
    prompt_manager.render = MagicMock(return_value="PROMPT")
    return prompt_manager


_SESSION_MS = 30 * 60_000  # 正经一场的时长（够常驻简报的"上一场最短时长"）


async def _ended_session(
    store: SQLiteDatabase, *, started: int = 1_000, ended: int | None = None, kind: str = "live"
) -> int:
    """造一场已结束的场次；不给结束时刻就按正经一场的时长算。"""
    pk = await store.sessions.insert_live_session(
        stream_id="room", platform="bilibili", started_at_ms=started, kind=kind
    )
    await store.sessions.close_live_session(
        live_session_id=pk, ended_at_ms=ended if ended is not None else started + _SESSION_MS
    )
    return pk


def _build_agent(
    store: SQLiteDatabase | None,
    llm: Any,
    tool_registry: ToolRegistry | None = None,
    *,
    session_manager: Any = None,
    **memory: Any,
) -> StreamerAgent:
    """装一个只接账本仓储的主播 Agent（旧三份记忆在本阶段已停写）。"""
    config = StreamerConfig.from_dict(
        {
            "proactive": {"enabled": False},
            "word_filter": {"enabled": False},
            "background": {"enabled": False},
            "memory": memory,
        }
    )
    repos: dict[str, Any] = {}
    if store is not None:
        repos = {
            "chat_repo": store.chat,
            "sessions_repo": store.sessions,
            "topic_repo": store.topics,
            "viewer_repo": store.viewers,
            "event_repo": store.events,
            "commitments_repo": store.commitments,
            "archive_repo": store.archive,
        }
    return StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=_prompts(),
        tool_registry=tool_registry,
        session_manager=session_manager,
        **repos,
    )


# ---------------------------------------------------------------------------
# 账本接线：回想工具与常驻简报
# ---------------------------------------------------------------------------


async def test_agent_registers_memory_recall_and_retired_tools_stay_gone(store: SQLiteDatabase) -> None:
    """阶段 3b 起只有一个记忆工具 memory_recall；旧的三个工具不再注册。"""
    registry = ToolRegistry()
    agent = _build_agent(store, FakeLLMManager(), tool_registry=registry)
    agent._register_tools()

    names = {spec.full_name for spec in registry.list_tools(for_agent="streamer")}
    assert "memory_recall" in names
    assert not names & {"memory_recall_session", "memory_recall_viewer", "streamer_notebook"}


async def test_refresh_brief_reads_previous_archive_and_pending_commitments(store: SQLiteDatabase) -> None:
    """常驻简报取上一场档案与不针对人、不挂游戏的未了结承诺。"""
    previous_pk = await _ended_session(store, started=1_000)
    await store.archive.insert_line(
        live_session_id=previous_pk,
        text="Minecraft：水车搭好了",
        quote="水车搭好啦",
        evidence_chat_id=1,
    )
    await store.commitments.insert_commitment(what="继续炸 TNT", quote="下次接着炸 TNT")
    # 对某位观众的、挂游戏的都不进常驻简报
    await store.commitments.insert_commitment(what="给 ta 盖房子", viewer_id=9)
    await store.commitments.insert_commitment(what="继续挖矿", game="Minecraft")

    agent = _build_agent(store, FakeLLMManager())
    await agent._refresh_memory_brief()

    brief = agent.memory_brief_text
    assert "上一场：" in brief and "水车搭好啦" in brief
    assert "下次接着炸 TNT" in brief
    assert "盖房子" not in brief and "继续挖矿" not in brief
    assert len(agent.memory_brief_commitment_ids) == 1


async def test_next_brief_reflects_operator_corrections(store: SQLiteDatabase) -> None:
    """运营者删一行档案、判一条承诺误记之后，下一场开播重拼的简报就少那几行。

    简报只依赖表快照、每场开播重拼一次：删掉的行在下一份简报里不再出现，没动的照旧在。
    """
    previous_pk = await _ended_session(store, started=1_000)
    kept_line = await store.archive.insert_line(
        live_session_id=previous_pk, text="水车搭好了", quote="水车搭好啦", evidence_chat_id=1
    )
    deleted_line = await store.archive.insert_line(
        live_session_id=previous_pk, text="答应过画头像", quote="答应你画头像", evidence_chat_id=2
    )
    kept_commitment = await store.commitments.insert_commitment(what="继续炸 TNT", quote="下次接着炸 TNT")
    voided_commitment = await store.commitments.insert_commitment(what="随口答应的", quote="回头再说")
    assert kept_line and deleted_line and kept_commitment and voided_commitment

    agent = _build_agent(store, FakeLLMManager())
    await agent._refresh_memory_brief()
    first = agent.memory_brief_text
    assert "水车搭好啦" in first and "下次接着炸 TNT" in first
    assert "答应你画头像" in first and "回头再说" in first

    assert await store.archive.delete_line(line_id=deleted_line) is True
    assert await store.commitments.void_commitment(commitment_id=voided_commitment) is True
    await agent._refresh_memory_brief()

    second = agent.memory_brief_text
    assert "水车搭好啦" in second and "下次接着炸 TNT" in second, "没删的行照旧在"
    assert "答应你画头像" not in second and "回头再说" not in second, "删掉/判误记的行下一份简报不再出现"


async def test_brief_rerender_is_byte_identical_when_ledger_unchanged(store: SQLiteDatabase) -> None:
    """每个决策窗都按表快照重渲简报：表里简报用到的部分没变时，前后两份逐字节相同。

    简报在 Planner 系统提示词的前段，逐字节相同前缀缓存才命中。简报不取的那些行（对某位
    观众的、挂游戏的承诺，彩排场的档案）变了也不该让它变；简报用到的行变了才变。
    """
    previous_pk = await _ended_session(store, started=1_000)
    await store.archive.insert_line(
        live_session_id=previous_pk, text="Minecraft：水车搭好了", quote="水车搭好啦", evidence_chat_id=1
    )
    await store.archive.insert_line(
        live_session_id=previous_pk, text="答应过画头像", quote="答应你画头像", evidence_chat_id=2
    )
    await store.commitments.insert_commitment(what="继续炸 TNT", quote="下次接着炸 TNT", now_ms_value=100)
    await store.commitments.insert_commitment(what="接着讲鬼故事", quote="下次讲完鬼故事", now_ms_value=100)

    agent = _build_agent(store, FakeLLMManager())
    await agent._refresh_memory_brief()
    first, first_ids = agent.memory_brief_text, agent.memory_brief_commitment_ids
    assert first

    # 简报不取的行：对某位观众的承诺、挂游戏的承诺、彩排场的档案
    await store.commitments.insert_commitment(what="给 ta 盖房子", viewer_id=9, now_ms_value=200)
    await store.commitments.insert_commitment(what="继续挖矿", game="Minecraft", now_ms_value=200)
    rehearsal_pk = await _ended_session(store, started=3_000, ended=4_000, kind="rehearsal")
    await store.archive.insert_line(
        live_session_id=rehearsal_pk, text="彩排试了新皮肤", quote="试试新皮肤", evidence_chat_id=3
    )
    await agent._refresh_memory_brief()
    assert agent.memory_brief_text.encode("utf-8") == first.encode("utf-8")
    assert agent.memory_brief_commitment_ids == first_ids

    # 简报用到的行变了，下一窗就变
    await store.commitments.insert_commitment(what="教大家搭电梯", quote="下次教你们搭电梯", now_ms_value=300)
    await agent._refresh_memory_brief()
    assert agent.memory_brief_text != first and "下次教你们搭电梯" in agent.memory_brief_text


async def test_brief_is_empty_without_ledger_material(store: SQLiteDatabase) -> None:
    agent = _build_agent(store, FakeLLMManager())
    await agent._refresh_memory_brief()
    assert agent.memory_brief_text == "" and agent.memory_brief_commitment_ids == ()


async def test_brief_drops_commitment_once_cited_in_this_session(store: SQLiteDatabase) -> None:
    """本场她已经点名提过的承诺，下一份简报不再列（与线索卡的用上即停同口径）；确知没送达的
    那句不算提过。没在直播时没有"本场"，整份照列。"""
    previous = await _ended_session(store, started=1_000)
    cited = await store.commitments.insert_commitment(
        what="接着救墓碑女仆", quote="下次接着救", created_session_id=previous
    )
    other = await store.commitments.insert_commitment(
        what="教大家搭电梯", quote="下次教你们搭电梯", created_session_id=previous
    )
    assert cited is not None and other is not None
    current = await store.sessions.insert_live_session(stream_id="room", platform="bilibili", started_at_ms=5_000_000)
    session = ActiveSessionStub(active_pk=current)
    agent = _build_agent(store, FakeLLMManager(), session_manager=session)

    async def _line(text: str, *, refs: str, dropped: bool = False) -> None:
        await store.chat.insert_live_chat(
            live_session_id=current,
            timestamp_ms=5_000_100,
            sender_role="assistant",
            content=text,
            message_type="speak",
            utterance_id=f"utt_{text}",
            dropped_at_ms=5_000_200 if dropped else None,
            memory_refs=refs,
        )

    await _line("电梯的事我记着", refs=format_memory_refs([other]), dropped=True)
    await agent._refresh_memory_brief()
    assert "下次接着救" in agent.memory_brief_text and "下次教你们搭电梯" in agent.memory_brief_text

    await _line("墓碑那事我还记着", refs=format_memory_refs([cited]))
    await agent._refresh_memory_brief()
    assert "下次接着救" not in agent.memory_brief_text, "本场提过一次就不再列"
    assert "下次教你们搭电梯" in agent.memory_brief_text, "没送达的那句不算提过"
    assert agent.memory_brief_commitment_ids == (other,)

    session.active_pk = None
    await agent._refresh_memory_brief()
    assert "下次接着救" in agent.memory_brief_text


async def test_brief_skips_false_start_session_but_keeps_its_commitment(store: SQLiteDatabase) -> None:
    """开错后马上重开：那一小段（1.5 分钟）也整理出了档案行和一条承诺。简报的"上一场"跳过它、
    取前面正经的那一场；它登记的承诺照常列着，时间词按"之前"写（不是简报里的那个上一场）。"""
    real = await _ended_session(store, started=1_000)
    await store.archive.insert_line(live_session_id=real, text="钻石剑做好了", quote="钻石剑到手", evidence_chat_id=1)
    false_start = await _ended_session(store, started=5_000_000, ended=5_090_000)
    await store.archive.insert_line(
        live_session_id=false_start, text="墓碑女仆救援留待下次", quote="下次接着救", evidence_chat_id=2
    )
    await store.commitments.insert_commitment(
        what="接着救墓碑女仆", quote="下次接着救", created_session_id=false_start, evidence_chat_id=2
    )

    agent = _build_agent(store, FakeLLMManager())
    await agent._refresh_memory_brief()

    brief = agent.memory_brief_text
    assert "钻石剑到手" in brief and "墓碑女仆救援留待下次" not in brief
    assert "「下次接着救」（之前）" in brief


async def test_open_start_settle_is_scheduled_in_background(store: SQLiteDatabase) -> None:
    """开播回调不能同步等整理：补整理排后台，回调本身立即返回，模型调用随后才发生。"""
    pending = await _ended_session(store, started=1_000)
    await store.chat.insert_live_chat(
        live_session_id=pending,
        timestamp_ms=1_500,
        sender_role="assistant",
        content="下次接着讲鬼故事",
        message_type="speech",
    )
    gate = asyncio.Event()
    llm = FakeLLMManager(response=Response(success=True, content='{"archive": []}'), gate=gate)
    agent = _build_agent(store, llm)

    agent._settle_pending_before_brief()

    assert llm.calls == [], "开播回调返回时不该已经等过模型"
    await asyncio.wait_for(llm.gate_reached.wait(), timeout=5)
    tasks = list(agent._memory_tasks)
    gate.set()
    assert tasks
    await asyncio.gather(*tasks)
    row = await store.sessions.get_live_session(live_session_id=pending)
    assert row is not None and row.settled_at_ms is not None, "后台补整理应把待整理场次写完"


# ---------------------------------------------------------------------------
# 点名链路：承诺卡 → use_memory → 台词落库带引用 → 本场不再推这张卡
# ---------------------------------------------------------------------------


class _AssistantWrites:
    """记账器用的聊天仓储：写库原样交给真实仓储，另记下写进库的主播台词条数。

    记账器在自己的订阅任务里写库，测试拿不到那个任务；包这一层只为知道"这句已经写完"，
    好用共享等待助手等它。其余方法原样转给真实仓储。
    """

    def __init__(self, chat: ChatRepo) -> None:
        self._chat = chat
        self.assistant_lines = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._chat, name)

    async def insert_live_chat(self, **fields: Any) -> Any:
        result = await self._chat.insert_live_chat(**fields)
        if fields.get("sender_role") == "assistant":
            self.assistant_lines += 1
        return result


def _danmaku_from_xiaoyu(text: str, message_id: str) -> RoomMessagePayload:
    return RoomMessagePayload(
        message_type="danmaku",
        platform="bilibili",
        message_id=message_id,
        user=RoomMessageUser(id="u_xiaoyu", name="小鱼"),
        content=text,
        timestamp_ms=now_ms(),
    )


async def _cards_for(store: SQLiteDatabase, session_pk: int, message: RoomMessagePayload) -> str:
    """一个新的线索推送实例（没有本场推送记录）对这条弹幕给出的线索块。"""
    cues = MemoryCueProvider(
        commitments_repo=store.commitments,
        chat_repo=store.chat,
        viewer_repo=store.viewers,
        current_session_provider=lambda: session_pk,
    )
    return (await cues.collect(batch=[message])).text


async def test_use_memory_lands_with_the_line_and_stops_the_card(store: SQLiteDatabase) -> None:
    """老观众开口 → 推对 ta 的承诺卡 → Planner 用 use_memory 点名 → 台词带着引用落库 →
    本场再看到 ta，这张卡不再推（用上即停）。

    "停推"用一个新的线索推送实例判定：它没有本场推送记录，限频挡不住它，只有库里
    那条引用能让它不推——证明停推靠的是落库的引用，不是内存里的推送计数。
    """
    bus = EventBus()
    manager = LiveSessionManager(store.sessions, store.chat, bus, platform="bilibili", room_id="room")
    await manager.start()
    bus.add_interceptor(SessionStampInterceptor(manager))
    writes = _AssistantWrites(store.chat)
    ledger = StorageLedger(bus, writes, store.viewers, store.events, session_manager=manager)  # type: ignore[arg-type]
    await ledger.start()
    speeches: List[StreamerSpeechPayload] = []

    async def _on_speech(event_name: str, payload: StreamerSpeechPayload, source: str) -> None:
        speeches.append(payload)

    bus.on(CoreEvents.STREAMER_SPEECH, _on_speech, model_class=StreamerSpeechPayload)

    # 小鱼是老观众，上一场答应过给 ta 画头像
    resolution = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_xiaoyu",
        user_name="小鱼",
        timestamp_ms=1_000,
        name_match_window_ms=0,
    )
    previous_pk = await _ended_session(store, started=1_000)
    commitment_id = await store.commitments.insert_commitment(
        what="给小鱼画头像",
        quote="下次给你画头像",
        viewer_id=resolution.viewer_id,
        created_session_id=previous_pk,
        now_ms_value=1_500,
    )
    assert commitment_id is not None
    number = render_commitment_number(commitment_id)

    planner_steps = [
        Response(
            success=True,
            content="",
            tool_calls=[
                ToolCall(
                    id="call_reply",
                    name="streamer_reply",
                    arguments={
                        "topic_summary": "回应小鱼",
                        "reply_guidance": "提一句答应过的头像",
                        "is_novel": True,
                        "use_memory": number,
                    },
                )
            ],
        ),
        Response(success=True, content=""),
    ]
    llm = MagicMock()

    async def _generate(*args: Any, **kwargs: Any) -> Response:
        if kwargs.get("profile") == "planner":
            return planner_steps.pop(0) if planner_steps else Response(success=True, content="")
        return Response(success=True, content="小鱼来啦，头像我记着呢")

    llm.generate = AsyncMock(side_effect=_generate)
    agent = make_streamer_agent(
        {"background": {"enabled": False}, "mouth": {"enabled": False}},
        llm=llm,
        event_bus=bus,
        tool_registry=ToolRegistry(),
        session_manager=manager,
        chat_repo=store.chat,
        viewer_repo=store.viewers,
        commitments_repo=store.commitments,
        archive_repo=store.archive,
    )
    await agent.start()
    try:
        pk = await manager.open_session(kind="live", title="画画")
        await wait_until(agent.is_live_active, what="开播回调跑完")
        first = _danmaku_from_xiaoyu("主播还记得我吗", "m1")
        assert number in await _cards_for(store, pk, first), "点名之前，对小鱼的承诺卡照常推"

        await bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, first, source="bilibili")
        await wait_until(lambda: writes.assistant_lines >= 1, what="点名的那句台词写进库")
    finally:
        await agent.stop()
        await ledger.stop()
        await bus.cleanup()

    planner_requests = [
        str(call.args[0]) for call in llm.generate.await_args_list if call.kwargs.get("profile") == "planner"
    ]
    assert number in planner_requests[0], "Planner 这一窗看得到承诺卡"
    assert [speech.memory_refs for speech in speeches] == [[number]], "台词带着点名的编号发出"
    stored_refs = await store.chat.list_session_memory_refs(live_session_id=pk)
    assert [memory_ref_numbers(raw) for raw in stored_refs] == [{commitment_id}], "引用随台词落库"
    second = _danmaku_from_xiaoyu("今天画什么呀", "m2")
    assert number not in await _cards_for(store, pk, second), "本场已经用上，再看到小鱼也不推这张卡"
