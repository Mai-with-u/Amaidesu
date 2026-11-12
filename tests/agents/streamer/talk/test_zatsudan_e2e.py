"""纯杂谈出道回端到端回归：零弹幕推进提纲 + 观众弹幕插话打断接回 + 棉花糖提问箱抽取。

经真实 SQLiteDatabase、EventBus、ToolRegistry、AgentManager、PromptManager 与
StreamerAgent 全链路驱动（只把 LLM 与 TTS 换成可编排替身），逐项核对：
1. 无游戏装配时委派四件工具对主播隐藏，主动窗与表达层不出现游戏残留；
2. 零弹幕场景按提纲要点（beats）推进，要点未完时提前调用 rundown_control(next) 被护栏拦下；
3. 杂谈档多句台词拆成子句共用段落号连播；播完第一句前观众发弹幕插话会收住后半句并记下尾句，
   下一轮空闲窗参考段出现"上一轮被打断没说完"提醒后顺利接回并切入提问箱环节；
4. 提问箱环节自动预摆未用卡片，支持 talk_draw_cards 抽取与 use_card_id 落库标已用。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import pytest

from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.agents.control import build_agent_control_provider
from src.modules.agents.deps import AgentDeps
from src.modules.agents.manager import AgentManager
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveStartedPayload
from src.modules.events.payloads.utterance import UtteranceDroppedPayload
from src.modules.llm.payload import Response, ToolCall
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.rundown import Rundown, RundownSegment
from src.modules.storage.models.talk_card import NewTalkCard
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger
from tests.modules.llm.fakes import FakeLLMManager


class _StubSessionManager:
    """暴露 active_pk 与 resolve_pk 的场次管理器替身。"""

    def __init__(self, pk: int) -> None:
        self.active_pk: Optional[int] = pk

    async def resolve_pk(self) -> Optional[int]:
        return self.active_pk


class _GatedTTSEngine:
    """首句可门控挂起、其余句即刻完成的 TTS 引擎替身：编排"讲到第一句时观众插话打断后半段"。"""

    def __init__(self) -> None:
        self.spoken: List[str] = []
        self.hold_phrase: str = ""
        self.first_clause_started = asyncio.Event()
        self.release_first_clause = asyncio.Event()
        self.second_clause_spoken = asyncio.Event()

    async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
        self.spoken.append(text)
        if self.hold_phrase and self.hold_phrase in text:
            self.first_clause_started.set()
            await self.release_first_clause.wait()
        elif self.first_clause_started.is_set():
            self.second_clause_spoken.set()


def _planner_reply(
    call_id: str,
    *,
    topic_summary: str,
    reply_guidance: str = "",
    target: Optional[str] = None,
    beat_done: bool = False,
    use_card_id: Optional[int] = None,
    extra_calls: Optional[List[ToolCall]] = None,
) -> Response:
    args: Dict[str, Any] = {
        "topic_summary": topic_summary,
        "reply_guidance": reply_guidance,
        "emotion": "happy",
        "intensity": 0.7,
        "is_novel": True,
    }
    if target is not None:
        args["target"] = target
    if beat_done:
        args["beat_done"] = True
    if use_card_id is not None:
        args["use_card_id"] = use_card_id
    calls = list(extra_calls or []) + [ToolCall(id=call_id, name="streamer_reply", arguments=args)]
    return Response(success=True, content="", tool_calls=calls, request_id=f"req_{call_id}")


def _replyer_line(req_id: str, text: str) -> Response:
    return Response(success=True, content=text, request_id=req_id)


def _last_user_content(call: Dict[str, Any]) -> str:
    return [m["content"] for m in call["input"] if m["role"] == "user"][-1]


@pytest.fixture
def prompts() -> Iterator[Any]:
    reset_prompt_manager()
    yield get_prompt_manager()
    reset_prompt_manager()


async def test_pure_zatsudan_debut_rehearsal_end_to_end(tmp_path: Path, prompts: Any) -> None:
    """零弹幕推进提纲 → 观众弹幕插话打断接回 → 切入提问箱抽取并落库标已用。"""
    db = SQLiteDatabase(tmp_path / "zatsudan_e2e.db")
    await db.initialize()

    profile_path = tmp_path / "stage_profile.md"
    profile_path.write_text(
        "## 身份与来历\n四亿年前古鱼与柑橘混合，靠抗氧化活到今天后化身为 AI 主播。\n\n"
        "## 舞台形象\n### [vts_stage] 二代舞台形象\n头顶两片绿叶，腰间挂着一颗参数调节旋钮。\n",
        encoding="utf-8",
    )

    rundown = Rundown(
        rundown_id="e2e_debut",
        title="端到端杂谈测试单",
        segments=[
            RundownSegment(
                id="intro",
                title="自我介绍与形象导览",
                task_description="向直播间观众介绍来历与舞台形象细节",
                expected_ms=300_000,
                mode="talk",
                beats=["名字与打招呼", "舞台形象绿叶与腰间旋钮"],
            ),
            RundownSegment(
                id="qna",
                title="棉花糖提问箱互动",
                task_description="抽取提问箱卡片回答观众投递的问题",
                expected_ms=300_000,
                mode="talk",
                beats=["抽取第一张提问箱卡片破冰"],
            ),
        ],
    )
    await db.rundowns.upsert_rundown(rundown)
    created_cards = await db.talk_cards.add_cards(
        [
            NewTalkCard(
                kind="marshmallow",
                title="平时最喜欢吃什么水果",
                body="听说和橙子很有渊源，平时最偏爱哪种柑橘？",
                asker="匿名观众",
            ),
            NewTalkCard(
                kind="opinion",
                title="甜豆腐脑还是咸豆腐脑",
                body="二选一直球表态时间！",
                asker="观众甲",
            ),
        ]
    )
    first_card_id = created_cards[0].id

    # 按调用顺序排好 Planner 与 Replyer 的应答序列：
    # 轮 1（零弹幕主动）：试图提前 next（被未完提纲拦下）+ 讲完第 1 个要点（beat_done=True）
    # 轮 2（零弹幕主动）：讲第 2 个要点，吐出三句杂谈台词（第 1 句播放时门控挂起）
    # 轮 3（观众甲插话）：在第一句播放期间收到弹幕，回观众甲带称呼与反问钩子，收住第 2、3 句记入尾句
    # 轮 4（零弹幕主动）：看到"上一轮被打断没说完"把旋钮补完，且提纲全完顺利 next 切进 qna 环节
    # 轮 5（零弹幕主动，qna）：第 1 步调 talk_draw_cards，第 2 步传 use_card_id 回答提问箱并标已用
    llm = FakeLLMManager(
        responses=[
            # 轮 1：Planner -> Replyer
            _planner_reply(
                "r1",
                topic_summary="出道开场向全场打招呼并介绍四亿年古鱼与柑橘来历",
                beat_done=True,
                extra_calls=[ToolCall(id=" premature_next", name="rundown_control", arguments={"action": "next"})],
            ),
            _replyer_line("rep_1", "大家晚上好呀，我是刚上台的麦麦！四亿年前和柑橘混在一块才活到今天呢。"),
            # 轮 2：Planner -> Replyer（三句杂谈段落，测试连播与中途插话打断）
            _planner_reply(
                "r2",
                topic_summary="展示二代舞台形象头顶的两片绿叶与腰间的参数调节旋钮",
                beat_done=True,
            ),
            _replyer_line(
                "rep_2",
                "你们看我头顶这两片绿叶还会晃呢。腰间这颗参数旋钮可是关键机关！千万别趁我不注意乱拧它~",
            ),
            # 轮 3（观众甲弹幕插话）：Planner -> Replyer
            _planner_reply(
                "r3",
                topic_summary="观众甲询问腰间旋钮能不能拧，解释拧动效果并顺势反问观众甲",
                target="debug_观众甲",
            ),
            _replyer_line("rep_3", "观众甲眼好尖呀，问腰上旋钮能不能拧——拧歪了我会开始胡说八道，你真想试一下吗？"),
            # 轮 4（零弹幕主动接回尾句并切进 qna）：Planner -> Replyer
            _planner_reply(
                "r4",
                topic_summary="顺嘴补完刚才被打断的腰间旋钮话题，收束形象导览并进入棉花糖提问箱环节",
                extra_calls=[ToolCall(id="go_qna", name="rundown_control", arguments={"action": "next"})],
            ),
            _replyer_line("rep_4", "刚才说到腰间那颗旋钮千万别乱拧嘛，好啦形象看完，咱们来开棉花糖提问箱吧！"),
            # 轮 5（qna 抽提问箱）：Planner step 1 (talk_draw_cards) -> Planner step 2 (reply with use_card_id) -> Replyer
            Response(
                success=True,
                content="",
                tool_calls=[
                    ToolCall(
                        id="draw_1",
                        name="talk_draw_cards",
                        arguments={"kind": "marshmallow", "limit": 2},
                    )
                ],
                request_id="req_draw_1",
            ),
            _planner_reply(
                "r5",
                topic_summary="回答匿名观众投递的提问箱问题：平时最喜欢吃什么柑橘水果",
                use_card_id=first_card_id,
                beat_done=True,
            ),
            _replyer_line("rep_5", "抽到匿名观众塞的棉花糖啦，问最爱吃什么水果——那当然是甜橙，苹果可排不上号！"),
        ]
    )

    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    ledger = TaskLedger(event_bus=bus)
    session_mgr = _StubSessionManager(pk=101)
    tts_engine = _GatedTTSEngine()
    tts_engine.hold_phrase = "头顶这两片绿叶"

    manager = AgentManager(
        tool_registry=registry,
        rundown_repo=db.rundowns,
        rundown_instance_repo=db.rundown_instances,
        rundown_transition_repo=db.rundown_transitions,
        chat_repo=db.chat,
        sessions_repo=db.sessions,
        topic_repo=db.topics,
        viewer_repo=db.viewers,
        talk_card_repo=db.talk_cards,
    )
    registry.register_provider(build_agent_control_provider(manager, ledger))

    try:
        enabled = await manager.enable_agent(
            "streamer",
            {
                "rundown_id": "e2e_debut",
                "proactive": {"enabled": False},
                "background": {"enabled": False},
                "mouth": {"enabled": False},
                "word_filter": {"enabled": False},
                "persona": {
                    "bot_name": "麦麦",
                    "personality": "爱唠嗑的舞台主播",
                    "profile_file": str(profile_path),
                    "active_outfit": "vts_stage",
                },
            },
            deps=AgentDeps(
                llm_manager=llm,
                prompt_manager=prompts,
                event_bus=bus,
                tool_registry=registry,
                tts_engine=tts_engine,
                speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 2000, "wait_playback": True},
                session_manager=session_mgr,
                task_ledger=ledger,
            ),
        )
        assert enabled is True
        agent = manager.get_agent_by_name("streamer")
        assert isinstance(agent, StreamerAgent)
        assert agent.current_speech_mode == "talk"

        # 纯杂谈场工具面核对：有 rundown_control 与 talk_draw_cards，没有委派四件
        visible_tools = {spec.full_name for spec in registry.list_tools(for_agent="streamer")}
        assert {"streamer_reply", "rundown_control", "talk_draw_cards"} <= visible_tools
        assert "framework_delegate" not in visible_tools

        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=101, source="manual", title="纯杂谈出道彩排"),
            source="test",
        )
        await bus.wait_until_drained()

        # ---- 轮 1：零弹幕推进第 1 个提纲要点，提前 next 被防早退护栏拦住 ----
        r1 = await agent.debug_test_decision(proactive=True)
        assert r1["speech"].startswith("大家晚上好呀")
        view_after_r1 = agent.get_rundown_view()
        assert view_after_r1 is not None and view_after_r1["snapshot"]["current"]["id"] == "intro"
        assert agent.talk_workbench.beat_statuses() == ["done", "pending"]

        planner_calls = [c for c in llm.calls if c.get("profile") == "planner"]
        replyer_calls = [c for c in llm.calls if c.get("profile") == "replyer"]
        assert "舞台杂谈模式" in planner_calls[0]["input"][0]["content"]
        assert "二代舞台形象" in replyer_calls[0]["system"]
        assert "游戏里刚有人说的话" not in replyer_calls[0]["input"][0]["content"]

        # ---- 轮 2：零弹幕讲第 2 个要点（拆成 3 句入播放队列，第 1 句门控挂起）----
        r2 = await agent.debug_test_decision(proactive=True)
        assert "腰间这颗参数旋钮" in r2["speech"]
        assert agent.talk_workbench.beat_statuses() == ["done", "done"]
        await asyncio.wait_for(tts_engine.first_clause_started.wait(), timeout=2.0)

        # ---- 轮 3：第 1 句尚在播放时观众甲插话 → 后两句作废进尾句，等第 1 句放行后立刻接观众甲 ----
        segment_cut = asyncio.Event()

        async def _on_dropped(_name: str, payload: UtteranceDroppedPayload, _src: Optional[str] = None) -> None:
            if payload.reason == "segment_interrupted":
                segment_cut.set()

        bus.on(CoreEvents.TTS_UTTERANCE_DROPPED, _on_dropped, model_class=UtteranceDroppedPayload)
        r3_task = asyncio.create_task(
            agent.debug_test_decision(batch=[{"nickname": "观众甲", "text": "腰上那个旋钮能拧吗？"}])
        )
        await asyncio.wait_for(segment_cut.wait(), timeout=2.0)
        tts_engine.release_first_clause.set()
        r3 = await asyncio.wait_for(r3_task, timeout=2.0)
        assert r3["speech"].startswith("观众甲")
        await asyncio.wait_for(tts_engine.second_clause_spoken.wait(), timeout=2.0)

        # 验证第 2、3 句已被插话收住，从未送进 TTS 合成播放
        assert tts_engine.spoken[:3] == [
            "大家晚上好呀，我是刚上台的麦麦！",
            "四亿年前和柑橘混在一块才活到今天呢。",
            "你们看我头顶这两片绿叶还会晃呢。",
        ]
        assert tts_engine.spoken[3].startswith("观众甲眼好尖呀")

        # ---- 轮 4：空闲主动窗看到"上一轮被打断没说完"尾句提醒，补完并切进 qna 环节 ----
        tts_engine.hold_phrase = ""
        r4 = await agent.debug_test_decision(proactive=True)
        assert "刚才说到腰间那颗旋钮" in r4["speech"]
        planner_r4_ref = _last_user_content([c for c in llm.calls if c.get("profile") == "planner"][3])
        assert "上一轮被打断没说完：腰间这颗参数旋钮可是关键机关！千万别趁我不注意乱拧它~" in planner_r4_ref
        view_after_r4 = agent.get_rundown_view()
        assert view_after_r4 is not None and view_after_r4["snapshot"]["current"]["id"] == "qna"

        # ---- 轮 5：qna 环节自动预摆未用卡片 + 调 talk_draw_cards + use_card_id 落库标已用 ----
        r5 = await agent.debug_test_decision(proactive=True)
        assert "棉花糖" in r5["speech"]
        planner_qna_calls = [c for c in llm.calls if c.get("profile") == "planner"][4:]
        qna_ref = _last_user_content(planner_qna_calls[0])
        assert "【可抽的话题卡 / 提问箱】" in qna_ref
        assert "平时最喜欢吃什么水果" in qna_ref
        assert "上一轮被打断没说完" not in qna_ref

        used_card = await db.talk_cards.get_card(first_card_id)
        unused_card = await db.talk_cards.get_card(created_cards[1].id)
        assert used_card is not None and used_card.is_used_in(101) is True
        assert unused_card is not None and unused_card.is_used_in(101) is False
        assert agent.talk_workbench.beat_statuses() == ["done"]
    finally:
        await manager.stop_all()
        await manager.cleanup_all()
        await bus.cleanup()
        await db.close()
