"""嘴与决策环节对账（mouth_reconcile）：欢迎去重、口头流水与出声前四态裁决。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, List, Optional
from unittest.mock import MagicMock

import pytest

from src.agents.streamer.mouth import Trigger
from src.agents.streamer.mouth_reconcile import (
    ReconcileVerdict,
    SpokenEntry,
    SpokenLog,
    extract_player_join_name,
    mentions_name,
    reconcile_with_mouth,
    strip_duplicate_greetings,
)
from src.agents.streamer.planner import Planner, PlannerConfig, PlannerEnv
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.trigger_cause import TriggerCause, TriggerKind, TriggerSource
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.game_chat import GameChatPayload
from src.modules.events.payloads.live import LiveStartedPayload
from src.modules.events.payloads.planner import PlannerDecisionPayload
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.llm.payload import Response, ToolCall
from src.modules.tools.models import ToolExecutionResult, ToolSpec
from src.modules.tools.provider import make_provider_from_specs
from src.modules.tools.registry import ToolRegistry
from src.modules.utils.time_utils import now_ms
from tests.agents.streamer.helpers import make_streamer_agent
from tests.modules.llm.fakes import FakeLLMManager


@pytest.mark.parametrize(
    ("chat_line", "expected"),
    [
        ("系统消息：玩家甲加入了游戏", "玩家甲"),
        ("Player_abc加入了游戏", "Player_abc"),
        ("系统消息: Player_abc joined the game.", "Player_abc"),
        ("玩家 玩家甲：我也加入了游戏里的队伍", ""),
    ],
)
def test_extract_player_join_name_matches_only_join_notices(chat_line: str, expected: str) -> None:
    assert extract_player_join_name(chat_line) == expected


@pytest.mark.parametrize(
    ("name", "speech", "expected"),
    [
        ("Player_abc", "诶，Player 也进来了？欢迎欢迎！", True),
        ("观众乙Abc", "欢迎观众乙进来坐～", True),
        ("Player_abc", "今天天气真不错，先去砍树吧。", False),
    ],
)
def test_mentions_name_handles_shortened_nicknames(name: str, speech: str, expected: bool) -> None:
    assert mentions_name(name, speech) is expected


@pytest.mark.parametrize(
    ("speech", "welcomed", "expected_text", "expected_stripped"),
    [
        # 整条只有重复欢迎：剥空后调用方按重复处理不再发声
        ("诶，欢迎玩家甲上线呀，快来一起玩！", {"玩家甲"}, "", True),
        # 前半句顺口重复欢迎、后半句是新的游戏进展：只剥前半句欢迎，保留后半句
        ("欢迎 Player_abc 来啦！这边橡树苗还差两棵没种完。", {"Player_abc"}, "这边橡树苗还差两棵没种完。", True),
        # 正常互动里提到名字但不是欢迎招呼：原样保留不误剥
        ("玩家甲刚才递给我的铁镐还挺顺手的。", {"玩家甲"}, "玩家甲刚才递给我的铁镐还挺顺手的。", False),
    ],
)
def test_strip_duplicate_greetings_preserves_non_greeting_clauses(
    speech: str, welcomed: set[str], expected_text: str, expected_stripped: bool
) -> None:
    assert strip_duplicate_greetings(speech, welcomed) == (expected_text, expected_stripped)


def test_spoken_log_deduplicates_rapid_same_source_and_attaches_trigger() -> None:
    log = SpokenLog()
    log.record("先在岸边站稳看看。", 1_000, source="mouth")
    log.record("先在岸边站稳看看。", 1_050, source="mouth", trigger="换到了新世界")
    log.record("我去前头砍两棵橡树。", 2_000, source="planner")

    mouth_rows = log.mouth_since(900)
    assert len(mouth_rows) == 1
    assert mouth_rows[0].trigger == "换到了新世界"
    assert log.recent_texts() == ["先在岸边站稳看看。", "我去前头砍两棵橡树。"]


async def test_reconcile_skips_llm_when_mouth_was_silent() -> None:
    """常态快路径：本轮嘴没开口时零模型调用放行（FakeLLMManager 设 fail=True 若被调用即暴露）。"""
    llm = FakeLLMManager(fail=True, fail_error="嘴未开口不应调用模型")
    verdict = await reconcile_with_mouth(
        "先把这几块圆石垫在脚下。",
        mouth_entries=[],
        llm_service=llm,
    )
    assert verdict.action == "pass"
    assert verdict.speech == "先把这几块圆石垫在脚下。"
    assert llm.calls == []


@pytest.mark.parametrize(
    ("model_reply", "candidate", "expected_action", "expected_speech"),
    [
        ("放行", "那我顺路去看看前头的村庄。", "pass", "那我顺路去看看前头的村庄。"),
        ("重复", "刚才那只蜘蛛突然扑过来吓我一跳！", "duplicate", ""),
        (
            "保留：顺便把旁边的火把也补上。",
            "蜘蛛扑过来吓死我了！顺便把旁边的火把也补上。",
            "trim",
            "顺便把旁边的火把也补上。",
        ),
        (
            "作废：已经切入新世界站在河岸边，不再卡在门外",
            "怎么还卡在服务器门口进不去呀。",
            "contradicted",
            "",
        ),
    ],
)
async def test_reconcile_parses_four_verdicts_when_mouth_spoke(
    model_reply: str, candidate: str, expected_action: str, expected_speech: str
) -> None:
    llm = FakeLLMManager(responses=[Response(success=True, content=model_reply, request_id="req_rec")])
    mouth_entries = [SpokenEntry(at_ms=1_500, source="mouth", text="诶，换到新世界啦，先在岸边站稳看看周围。")]
    verdict = await reconcile_with_mouth(
        candidate,
        mouth_entries=mouth_entries,
        new_game_lines=["游戏·minecraft·milestone: 已进入新世界出生点河岸"],
        llm_service=llm,
    )
    assert verdict.action == expected_action
    assert verdict.speech == expected_speech
    assert len(llm.calls) == 1


def _reply_tool_call(call_id: str, summary: str) -> ToolCall:
    return ToolCall(
        id=call_id,
        name="streamer_reply",
        arguments={
            "target": "现场",
            "topic_summary": summary,
            "topic_tag": "现场进展",
            "reply_guidance": "对眼前的事接一句",
            "emotion": "happy",
            "emotion_intensity": 0.6,
            "is_novel": True,
        },
    )


@pytest.mark.parametrize(
    ("planner_draft", "expected_planner_speech", "expected_silent"),
    [
        # 第 65 场 24 秒双欢迎回归：整句只剩重复欢迎时静默收尾，不发第二条 streamer.speech
        ("欢迎玩家甲上线呀，快来一起种树！", None, "mouth_covered"),
        # 前半句顺口重复欢迎、后半句有新游戏进展：剥掉重复欢迎分句，只播新进展
        ("欢迎玩家甲上线呀！西坡这边还差四棵橡树苗没栽上。", "西坡这边还差四棵橡树苗没栽上。", None),
    ],
)
async def test_session65_game_join_duplicate_welcome_is_suppressed_or_trimmed_end_to_end(
    planner_draft: str, expected_planner_speech: str | None, expected_silent: str | None
) -> None:
    """第 65 场回归：公屏系统消息提示玩家加入游戏，嘴 3 秒内已开口欢迎后，20 秒后决策环节不再重复欢迎。"""
    bus = EventBus()
    spoken_events: List[str] = []

    async def _on_speech(_name: str, payload: StreamerSpeechPayload, _source: str) -> None:
        spoken_events.append(payload.text)

    bus.on(CoreEvents.STREAMER_SPEECH, _on_speech, model_class=StreamerSpeechPayload)
    registry = ToolRegistry()

    llm_responses = [
        # 1) 嘴当场反应：欢迎刚上线的玩家甲
        Response(success=True, content="happy：诶，玩家甲也进来了？欢迎欢迎，来一起栽树苗呗！", request_id="r_m1"),
        # 2) Planner ReAct 调 streamer_reply
        Response(success=True, content="", tool_calls=[_reply_tool_call("c1", "玩家甲加入游戏并看树苗")]),
        # 3) Replyer 生成初稿
        Response(success=True, content=planner_draft, request_id="r_rep1"),
    ]
    if expected_planner_speech is not None:
        # 剥掉欢迎后仍有新内容，若本轮嘴开口过则会走一次轻量核对放行新内容
        llm_responses.append(Response(success=True, content="放行", request_id="r_rec1"))

    llm = FakeLLMManager(responses=llm_responses)
    agent = make_streamer_agent({"mouth": {"enabled": True}}, llm=llm, event_bus=bus, tool_registry=registry)
    agent._register_tools()
    await agent._on_live_started(
        CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=1, started_at_ms=1_000), "test"
    )
    await agent._on_game_chat(
        CoreEvents.GAME_CHAT_RECEIVED,
        GameChatPayload(game="minecraft", kind="system", content="玩家甲加入了游戏", timestamp_ms=2_000),
        "mc",
    )

    assert agent._mouth is not None
    mouth_text = await agent._mouth.react_once(
        [Trigger(at_ms=now_ms(), key="game:milestone", text="树苗拿到手了", urgent=True, refresh_scene=False)]
    )
    await bus.wait_until_drained()
    assert mouth_text == "诶，玩家甲也进来了？欢迎欢迎，来一起栽树苗呗！"
    assert "玩家甲" in agent._room_state.welcomed_names

    result = await agent._rounds.execute(
        [],
        forced=False,
        trigger_cause=TriggerCause(kind=TriggerKind.GAME, source=TriggerSource.TICK),
        proactive=True,
    )
    await bus.wait_until_drained()

    assert result["speech"] == expected_planner_speech
    assert result["silent_reason"] == expected_silent
    expected_emitted = [mouth_text] if expected_planner_speech is None else [mouth_text, expected_planner_speech]
    assert spoken_events == expected_emitted


async def test_session65_2105_contradiction_bounces_planner_to_rethink_with_fresh_scene() -> None:
    """第 65 场 21:05:50/21:06:05 相邻矛盾回归：构思期间已换入新世界且嘴先说出"到新世界了"，
    旧台词"还卡在门口进不去"被拦下作废，携带最新现场退回 Planner 重想出与新世界一致的台词。"""
    clock_now = 10_000
    reply_drafts = iter(
        [
            "怎么还卡在服务器门口进不去呀，急死我了。",
            "新世界河岸这边视野真宽敞，我先去砍两棵树做工作台。",
        ]
    )

    async def _fake_reply(_inv: Any) -> ToolExecutionResult:
        nonlocal clock_now
        clock_now += 2_000
        return ToolExecutionResult(
            tool_name="streamer_reply",
            success=True,
            structured_content={
                "speech": next(reply_drafts),
                "emotion": {"name": "happy", "intensity": 0.6},
                "actions": [],
                "metadata": {"target": "现场"},
            },
        )

    reg = ToolRegistry()
    spec = ToolSpec(name="reply", description="主播发言出口", kind="sync", provider="streamer")
    reg.register_provider(make_provider_from_specs("streamer", [(spec, _fake_reply)]))

    reconcile_calls: List[tuple[str, int]] = []

    async def _reconciler(candidate: str, since_ms: int) -> ReconcileVerdict:
        reconcile_calls.append((candidate, since_ms))
        if "卡在服务器门口" in candidate:
            return ReconcileVerdict(action="contradicted", reason="已切入新世界站在河岸边，不再卡在门外")
        return ReconcileVerdict(action="pass", speech=candidate)

    async def _fresh_ctx(_since_ms: int) -> dict[str, Any]:
        return {
            "mouth_just_said": ["诶，换到新世界啦，先在岸边站稳看看周围。"],
            "latest_game_events": ["游戏·minecraft·milestone: 已进入新世界出生点河岸"],
            "latest_scene": "在新世界的河岸边，白天，晴。",
        }

    llm = FakeLLMManager(
        responses=[
            Response(success=True, content="", tool_calls=[_reply_tool_call("c1", "掉线卡在门外进不去")]),
            Response(success=True, content="", tool_calls=[_reply_tool_call("c2", "刚进新世界河岸准备砍树")]),
        ]
    )
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    planner = Planner(
        config=PlannerConfig(planner_max_steps=4),
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=reg,
        env=PlannerEnv(
            mouth_reconciler=_reconciler,
            fresh_context_since=_fresh_ctx,
            clock=lambda: clock_now,
        ),
    )

    outcome = await planner.plan([], proactive=True)

    assert outcome.replied is True
    assert outcome.speech == "新世界河岸这边视野真宽敞，我先去砍两棵树做工作台。"
    assert "卡在服务器门口" not in (outcome.speech or "")
    assert len(reconcile_calls) == 2
    # 第一步被退回后的 tool 观察（紧跟在第一步 assistant 调用之后）携带嘴刚说的话与最新现场
    all_msgs = llm.calls[1]["input"]
    bounced_tool_msgs = [m for m in all_msgs if m.get("role") == "tool" and '"ok": false' in m.get("content", "")]
    assert len(bounced_tool_msgs) == 1
    tool_obs = json.loads(bounced_tool_msgs[0]["content"])
    assert tool_obs["ok"] is False
    assert tool_obs["mouth_just_said"] == ["诶，换到新世界啦，先在岸边站稳看看周围。"]
    assert "新世界的河岸边" in tool_obs["latest_scene"]


async def test_playback_slot_before_emit_vetoes_stale_speech_when_mouth_spoke_during_slot_wait() -> None:
    """毫秒时序回归（关口二）：Replyer 生成完时尚无嘴插话，但在等前一句 TTS 播放空档期间嘴先回应了新事件，
    占住播放槽位后出声前 before_emit 拦下过时台词，记 mouth_superseded 并唤醒下一拍重评。"""
    bus = EventBus()
    spoken_events: List[str] = []
    decisions: List[PlannerDecisionPayload] = []

    async def _on_speech(_name: str, payload: StreamerSpeechPayload, _source: str) -> None:
        spoken_events.append(payload.text)

    async def _on_decision(_name: str, payload: PlannerDecisionPayload, _source: str) -> None:
        decisions.append(payload)

    bus.on(CoreEvents.STREAMER_SPEECH, _on_speech, model_class=StreamerSpeechPayload)
    bus.on(CoreEvents.PLANNER_DECISION, _on_decision, model_class=PlannerDecisionPayload)
    registry = ToolRegistry()

    first_tts_started = asyncio.Event()
    release_first_tts = asyncio.Event()
    replyer_generated = asyncio.Event()

    class _GatedTTS:
        async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
            del utterance_id
            if text == "上一句还在播放中。":
                first_tts_started.set()
                await release_first_tts.wait()

    llm = FakeLLMManager(
        responses=[
            # 1) Planner 调 streamer_reply
            Response(success=True, content="", tool_calls=[_reply_tool_call("c1", "门外等重连")]),
            # 2) Replyer 生成完毕（此时嘴尚未说话，关口一零模型调用直接通过）
            Response(success=True, content="怎么还在门外转圈圈进不去呀。", request_id="r_rep"),
            # 3) 等播放槽位后出声前关口二检测到这期间嘴刚开口，核对判定作废
            Response(success=True, content="作废：已进入新世界出生点，不再在门外转圈", request_id="r_rec_slot"),
        ]
    )
    orig_generate = llm.generate

    async def _tracked_generate(input_data: Any, **kwargs: Any) -> Response:
        resp = await orig_generate(input_data, **kwargs)
        if resp.request_id == "r_rep":
            replyer_generated.set()
        return resp

    llm.generate = _tracked_generate  # type: ignore[method-assign]

    agent = make_streamer_agent(
        {"mouth": {"enabled": True}},
        llm=llm,
        event_bus=bus,
        tool_registry=registry,
        tts_engine=_GatedTTS(),
        speech_config={"enabled": True, "wait_playback": True},
    )
    agent._register_tools()
    await agent._speech.start()
    try:
        await agent._on_live_started(
            CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=1, started_at_ms=1_000), "test"
        )
        # 先让前一句占住 TTS 播放队列
        await agent._speech.dispatch({"speech": "上一句还在播放中。", "emotion": {"name": "neutral", "intensity": 0.5}})
        await first_tts_started.wait()

        # 启动本轮决策：Replyer 生成完后卡在等前一句 TTS 播完的背压闸上
        round_task = asyncio.create_task(
            agent._rounds.execute(
                [],
                forced=False,
                trigger_cause=TriggerCause(kind=TriggerKind.COLD, source=TriggerSource.TICK),
                proactive=True,
            )
        )
        await replyer_generated.wait()
        await asyncio.sleep(0)

        # 等播放槽位期间，新世界里程碑到达且嘴说出了新世界反应，随后释放前一句播放完毕
        await agent._on_game_event(
            CoreEvents.GAME_MILESTONE,
            GamePayload(game="minecraft", event_type="milestone", message="已进入新世界河岸"),
            "minecraft",
        )
        agent._spoken_log.record("诶，换到新世界啦！", at_ms=9_999_999_999_999, source="mouth", trigger="进入新世界")
        release_first_tts.set()

        result = await round_task
        await bus.wait_until_drained()
    finally:
        await agent._speech.stop()

    assert result["speech"] is None
    assert result["silent_reason"] == "mouth_superseded"
    # 只播出了占槽位的前一句，过时的"还在门外转圈圈"被关口二拦下未发事件
    assert spoken_events == ["上一句还在播放中。"]
    assert decisions[-1].should_reply is False
    assert decisions[-1].silent_reason == "mouth_superseded"
    assert agent._proactive_signals.game_pending is True


async def test_planner_receives_realtime_mouth_speeches_in_speech_history() -> None:
    """嘴刚说出口的反应实时并入下一轮表达侧的已说台词列表（即使尚未落库或无库模式也立即可见）。"""
    captured_attachments: List[Any] = []

    async def _capture_reply(inv: Any) -> ToolExecutionResult:
        captured_attachments.append(inv.attachment)
        return ToolExecutionResult(
            tool_name="streamer_reply",
            success=True,
            structured_content={
                "speech": "那我先把船收进背包。",
                "emotion": {"name": "neutral", "intensity": 0.5},
                "actions": [],
                "metadata": {"target": "现场"},
            },
        )

    reg = ToolRegistry()
    spec = ToolSpec(name="reply", description="主播发言出口", kind="sync", provider="streamer")
    reg.register_provider(make_provider_from_specs("streamer", [(spec, _capture_reply)]))
    llm = FakeLLMManager(
        responses=[Response(success=True, content="", tool_calls=[_reply_tool_call("c1", "收起橡木船")])]
    )
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    planner = Planner(
        config=PlannerConfig(),
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=reg,
        env=PlannerEnv(recent_speeches_provider=lambda: ["哎呀工作台摆水里去了！"]),
    )

    await planner.plan([], history=[], proactive=True)

    assert len(captured_attachments) == 1
    assert "哎呀工作台摆水里去了！" in captured_attachments[0].speech_history


async def test_contradiction_bounce_records_mouth_superseded_when_planner_chooses_silence() -> None:
    """原台词因新现场矛盾被退回后，若 Planner 判断嘴已交代清楚、第二步不再调用工具，静默原因记为 mouth_superseded。"""

    async def _fake_reply(_inv: Any) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name="streamer_reply",
            success=True,
            structured_content={
                "speech": "怎么还卡在服务器门口进不去呀。",
                "emotion": {"name": "sad", "intensity": 0.5},
                "actions": [],
                "metadata": {"target": "现场"},
            },
        )

    reg = ToolRegistry()
    spec = ToolSpec(name="reply", description="主播发言出口", kind="sync", provider="streamer")
    reg.register_provider(make_provider_from_specs("streamer", [(spec, _fake_reply)]))

    async def _reconciler(_candidate: str, _since_ms: int) -> ReconcileVerdict:
        return ReconcileVerdict(action="contradicted", reason="已切入新世界出生点")

    llm = FakeLLMManager(
        responses=[
            Response(success=True, content="", tool_calls=[_reply_tool_call("c1", "卡在门外")]),
            Response(success=True, content="", tool_calls=[]),
        ]
    )
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    planner = Planner(
        config=PlannerConfig(planner_max_steps=4),
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=reg,
        env=PlannerEnv(mouth_reconciler=_reconciler),
    )

    outcome = await planner.plan([], proactive=True)

    assert outcome.replied is False
    assert outcome.silent_reason == "mouth_superseded"
