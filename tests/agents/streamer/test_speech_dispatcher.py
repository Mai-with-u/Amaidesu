"""发言管线：SpeechDispatcher 把 reply 结构化结果扇出到 streamer.speech 业务事件 / 字幕 / TTS 队列。

业务事件与 TTS 启用正交；三路共用同一 utterance_id；任何一路失败都不阻断决策循环。
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.planner import RoundOutcome
from src.agents.streamer.decision_executor import DecisionRoundExecutor
from src.agents.streamer.session_scope import WindowMaterials
from src.agents.streamer.speech_dispatcher import SpeechDispatcher
from src.agents.streamer.timeline import GameEventBatch
from src.agents.streamer.stats import StreamerStats
from src.agents.streamer.tools.reply_tool import ReplyToolProvider
from src.agents.streamer.trigger_cause import TriggerCause, TriggerKind
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.planner import PlannerDecisionPayload, StreamerStagePayload
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.utterance import UtteranceStartedPayload
from src.modules.tools.registry import ToolRegistry
from tests.agents.streamer.helpers import make_streamer_agent

_TTS_ON = {"enabled": True, "max_queue": 3, "render_timeout_ms": 1000}


class _Engine:
    """TTS 引擎替身：记录 handle_speech，可拖延模拟"合成+播放"全周期。"""

    def __init__(self, delay_s: float = 0.0) -> None:
        self.calls: list[tuple[str, Optional[str]]] = []
        self.delay_s = delay_s

    async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
        self.calls.append((text, utterance_id))
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)


def _capture_speech(bus: EventBus) -> list[tuple[float, StreamerSpeechPayload]]:
    captured: list[tuple[float, StreamerSpeechPayload]] = []

    async def _capture(event_name: str, payload: Any, source: Optional[str] = None) -> None:
        captured.append((time.monotonic(), payload))

    bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)
    return captured


async def _until(condition, timeout: float = 2.0) -> None:
    async def _poll() -> None:
        while not condition():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout)


@pytest.fixture
async def started():
    """启动后的 dispatcher 工厂，测试结束统一 stop。"""
    dispatchers: list[SpeechDispatcher] = []

    async def _make(*, bus=None, subtitle=None, engine=None, config=None) -> SpeechDispatcher:
        dispatcher = SpeechDispatcher(event_bus=bus, subtitle_service=subtitle, tts_engine=engine, speech_config=config)
        await dispatcher.start()
        dispatchers.append(dispatcher)
        return dispatcher

    yield _make
    for dispatcher in dispatchers:
        await dispatcher.stop()


async def test_dispatch_fans_out_with_shared_utterance_id(started) -> None:
    bus, engine = EventBus(), _Engine()
    captured = _capture_speech(bus)
    dispatcher = await started(bus=bus, engine=engine, config=_TTS_ON)

    result = await dispatcher.dispatch(
        {"speech": "你好", "emotion": {"name": "happy", "intensity": 0.8}},
        target_user_id="u1",
        reply_to_message_id="m9",
        round_id="rnd_1",
    )
    await _until(lambda: captured and engine.calls)

    speech, emotion, utterance_id = result
    assert (speech, emotion) == ("你好", "happy") and utterance_id.startswith("utt_")
    payload = captured[0][1]
    assert (payload.text, payload.utterance_id, payload.round_id) == ("你好", utterance_id, "rnd_1")
    assert (payload.target_user_id, payload.reply_to_message_id) == ("u1", "m9")
    assert (payload.emotion, payload.emotion_intensity) == ("happy", 0.8)
    assert engine.calls == [("你好", utterance_id)]
    assert dispatcher.utterance_queue.get_stats()["enqueued"] == 1


async def test_utterance_ids_increment_per_dispatch(started) -> None:
    dispatcher = await started()
    ids = [(await dispatcher.dispatch({"speech": f"第 {i} 句", "emotion": ""}))[2] for i in range(3)]
    assert all(re.fullmatch(r"utt_\d+_\d+", uid) for uid in ids)
    assert [int(uid.rsplit("_", 1)[1]) for uid in ids] == [1, 2, 3]
    assert dispatcher.utterance_seq == 3


async def test_empty_speech_and_non_dict_payload_are_dropped(started, loguru_capture) -> None:
    bus = EventBus()
    subtitle = MagicMock()
    subtitle.show = AsyncMock()
    captured = _capture_speech(bus)
    dispatcher = await started(bus=bus, subtitle=subtitle)

    for empty in ("", "   ", "\n\t  "):
        await dispatcher.dispatch({"speech": empty, "emotion": {"name": "happy", "intensity": 0.5}})
    # 非 dict（reply 工具失败的防御性兜底）：WARN 后跳过
    assert await dispatcher.dispatch(["speech", "emotion"]) is None
    await asyncio.sleep(0.05)

    assert captured == [] and subtitle.show.await_count == 0
    assert dispatcher.utterance_seq == 0
    assert any(r["level"] == "WARNING" and "非 dict" in r["message"] for r in loguru_capture.records)


@pytest.mark.parametrize(
    ("config", "engine"),
    [(None, _Engine()), ({"enabled": False}, _Engine()), (_TTS_ON, None)],
    ids=["default_off", "disabled", "enabled_without_engine"],
)
async def test_tts_off_still_emits_business_speech(started, config, engine) -> None:
    bus = EventBus()
    captured = _capture_speech(bus)
    dispatcher = await started(bus=bus, engine=engine, config=config)
    assert dispatcher.tts_enabled is False and dispatcher.utterance_queue is None

    result = await dispatcher.dispatch({"speech": "今天好冷", "emotion": ""})
    await _until(lambda: captured)

    payload = captured[0][1]
    assert result[0] == payload.text == "今天好冷"
    # 情绪必有值：空串按生产者同款语义规范化为 neutral
    assert (payload.emotion, payload.emotion_intensity) == ("neutral", 0.5)
    assert dispatcher.utterance_seq == 1


async def test_subtitle_failure_does_not_block_dispatch(started, loguru_capture) -> None:
    """直出模式下字幕 show 异常被吞（WARN 记录），派发路径不受影响。"""
    subtitle = MagicMock()
    subtitle.show = AsyncMock(side_effect=RuntimeError("simulated subtitle failure"))
    dispatcher = await started(bus=EventBus(), subtitle=subtitle, config=_TTS_ON)

    result = await dispatcher.dispatch({"speech": "字幕坏了", "emotion": ""})
    await _until(lambda: subtitle.show.await_count)
    assert result is not None
    assert any(r["level"] == "WARNING" and "字幕 show 异常" in r["message"] for r in loguru_capture.records)


# 字幕源择流：裁决在 dispatcher 构造期，两模式互斥


async def test_subtitle_modes_are_mutually_exclusive(started) -> None:
    """TTS 引擎在位 → 跟随播放事件驱动字幕，派发直出停用；无引擎 → 直出。"""
    bus, engine = EventBus(), _Engine()
    follow_subtitle = MagicMock()
    follow_subtitle.show = AsyncMock()
    follow_dispatcher = await started(bus=bus, subtitle=follow_subtitle, engine=engine, config=_TTS_ON)
    assert follow_dispatcher._subtitle_follows_playback is True

    # 派发一句：只走 TTS 队列，不直出字幕
    await follow_dispatcher.dispatch({"speech": "第一句", "emotion": {}})
    await _until(lambda: len(engine.calls) == 1)
    await asyncio.sleep(0.05)
    assert follow_subtitle.show.await_count == 0

    # 播放事件到达时字幕跟随显示
    await bus.emit(
        CoreEvents.TTS_UTTERANCE_STARTED,
        UtteranceStartedPayload(utterance_id=engine.calls[0][1], speech_text=engine.calls[0][0], engine="test"),
        source="test",
    )
    await _until(lambda: follow_subtitle.show.await_count == 1)
    assert follow_subtitle.show.await_args.args == ("第一句", engine.calls[0][1])

    # 无引擎：直出模式，派发即显示
    direct_subtitle = MagicMock()
    direct_subtitle.show = AsyncMock()
    direct_dispatcher = await started(bus=bus, subtitle=direct_subtitle, engine=None, config=_TTS_ON)
    assert direct_dispatcher._subtitle_follows_playback is False
    await direct_dispatcher.dispatch({"speech": "第二句", "emotion": {}})
    await _until(lambda: direct_subtitle.show.await_count == 1)
    assert direct_subtitle.show.await_args.args[0] == "第二句"


async def test_start_failure_falls_back_subtitle_to_direct(monkeypatch) -> None:
    """TTS 管线启动失败降级后：解绑播放跟随器、回切派发直出，字幕不全哑。"""
    bus, engine = EventBus(), _Engine()
    subtitle = MagicMock()
    subtitle.show = AsyncMock()
    dispatcher = SpeechDispatcher(event_bus=bus, subtitle_service=subtitle, tts_engine=engine, speech_config=_TTS_ON)
    assert dispatcher._subtitle_follows_playback is True

    class _BoomQueue:
        def __init__(self, **kwargs: Any) -> None:
            raise RuntimeError("队列起不来")

    monkeypatch.setattr("src.agents.streamer.speech_dispatcher.UtteranceQueue", _BoomQueue)
    await dispatcher.start()
    assert dispatcher.tts_enabled is False
    assert dispatcher._subtitle_follows_playback is False

    # 跟随器已退订：播放事件不再驱动字幕
    await bus.emit(
        CoreEvents.TTS_UTTERANCE_STARTED,
        UtteranceStartedPayload(utterance_id="utt_x", speech_text="不该显示", engine="test"),
        source="test",
    )
    await asyncio.sleep(0.05)
    assert subtitle.show.await_count == 0

    # 派发直出恢复：dispatch 即显示
    await dispatcher.dispatch({"speech": "直出这句", "emotion": {}})
    await _until(lambda: subtitle.show.await_count == 1)
    assert subtitle.show.await_args.args[0] == "直出这句"
    await dispatcher.stop()


async def test_stop_is_idempotent() -> None:
    dispatcher = SpeechDispatcher(event_bus=None, subtitle_service=None, tts_engine=_Engine(), speech_config=_TTS_ON)
    await dispatcher.start()
    assert dispatcher.utterance_queue is not None
    await dispatcher.stop()
    await dispatcher.stop()
    assert dispatcher.utterance_queue is None


# 发言等播放（背压闸）


def _playback_config(wait: bool, timeout_ms: int = 5000) -> dict:
    return {**_TTS_ON, "render_timeout_ms": 5000, "wait_playback": wait, "wait_playback_timeout_ms": timeout_ms}


@pytest.mark.parametrize(("wait", "min_gap", "max_gap"), [(True, 0.3, 9.9), (False, 0.0, 0.3)])
async def test_wait_playback_gates_next_speech(started, wait: bool, min_gap: float, max_gap: float) -> None:
    """开启时第二句的业务事件要等第一句播完（嘴空才放行）；关闭时连续 dispatch 不阻塞。"""
    bus, engine = EventBus(), _Engine(delay_s=0.4)
    captured = _capture_speech(bus)
    dispatcher = await started(bus=bus, engine=engine, config=_playback_config(wait))

    await dispatcher.dispatch({"speech": "第一句", "emotion": {}})
    await dispatcher.dispatch({"speech": "第二句", "emotion": {}})
    await _until(lambda: len(engine.calls) == 2 and len(captured) == 2)

    assert min_gap <= captured[1][0] - captured[0][0] < max_gap
    assert [text for text, _ in engine.calls] == ["第一句", "第二句"]


async def test_wait_playback_fails_open_on_timeout(started) -> None:
    """等播超时放行，不丢话也不无限阻塞决策循环。"""
    dispatcher = await started(engine=_Engine(delay_s=1.0), config=_playback_config(True, timeout_ms=150))
    await dispatcher.dispatch({"speech": "第一句", "emotion": {}})
    t0 = time.monotonic()
    assert (await dispatcher.dispatch({"speech": "第二句", "emotion": {}}))[0] == "第二句"
    assert 0.1 <= time.monotonic() - t0 < 0.9


# 后台扇出持有与停止汇合


async def test_stop_drains_inflight_fanout() -> None:
    """dispatch 后立即 stop：在飞扇出任务被汇合，不留 pending 任务。"""
    subtitle = MagicMock()
    subtitle.show = AsyncMock()
    dispatcher = SpeechDispatcher(
        event_bus=EventBus(), subtitle_service=subtitle, tts_engine=_Engine(), speech_config=_TTS_ON
    )
    await dispatcher.start()
    await dispatcher.dispatch({"speech": "你好", "emotion": {"name": "happy", "intensity": 0.8}}, round_id="rnd")
    await dispatcher.stop()

    assert all(t.done() for t in dispatcher._bg_tasks)
    await asyncio.sleep(0)  # 让 done 回调的 discard 执行
    assert dispatcher._bg_tasks == set()


@pytest.mark.parametrize("speak_seconds", [1.0, 60.0], ids=["slow", "hanging"])
async def test_dispatch_returns_immediately_and_stop_is_bounded(speak_seconds: float) -> None:
    """慢或挂死的 TTS 不阻塞 dispatch；stop 在汇合窗口内返回（挂死由队列取消机制截断）。"""
    speak_started = asyncio.Event()

    async def _slow_speak(text, utterance_id=None):
        speak_started.set()
        await asyncio.sleep(speak_seconds)

    engine = MagicMock()
    engine.handle_speech = AsyncMock(side_effect=_slow_speak)
    dispatcher = SpeechDispatcher(
        event_bus=None, subtitle_service=None, tts_engine=engine, speech_config={"enabled": True}
    )
    await dispatcher.start()

    t0 = time.monotonic()
    assert (await dispatcher.dispatch({"speech": "x", "emotion": {"name": "happy", "intensity": 0.8}}))[0] == "x"
    assert time.monotonic() - t0 < 0.1
    await asyncio.wait_for(speak_started.wait(), timeout=1.0)

    t1 = time.monotonic()
    await dispatcher.stop()
    assert time.monotonic() - t1 < 3.0
    assert dispatcher.utterance_queue is None


# 决策轮接线


async def test_speech_event_precedes_round_end_events() -> None:
    """streamer.speech 同步发出，先于本轮 planner.decision 与 idle 状态（控制台时间线依赖该顺序）。"""
    bus = EventBus()
    planner = MagicMock()
    planner.plan = AsyncMock(
        return_value=RoundOutcome(
            replied=True,
            target="m1",
            reply_to="m1",
            topic_summary="闲聊",
            reply_guidance="热情一点",
            reply_duration_ms=12,
            raw_content="RAW",
            llm_request_id="req_1",
            reply_payload={"speech": "你好呀", "emotion": {"name": "happy", "intensity": 0.5}, "actions": []},
        )
    )
    executor = DecisionRoundExecutor(
        planner=planner,
        speech=SpeechDispatcher(event_bus=bus, subtitle_service=None, tts_engine=None, speech_config=None),
        event_bus=bus,
        room_state=MagicMock(),
        proactive_trigger=MagicMock(),
        stats=StreamerStats(),
        thinking_sink=None,
        thinking_enabled=False,
        window_opener=AsyncMock(
            return_value=WindowMaterials(history=[], game_batch=GameEventBatch(), session_recap="")
        ),
        rundown_text_provider=MagicMock(return_value=None),
    )
    order: list[str] = []

    def _record(label: str):
        async def _capture(event_name: str, payload: Any, source: Optional[str] = None) -> None:
            order.append(label)

        return _capture

    bus.on(CoreEvents.STREAMER_STAGE, _record("stage"), model_class=StreamerStagePayload)
    bus.on(CoreEvents.STREAMER_SPEECH, _record("speech"), model_class=StreamerSpeechPayload)
    bus.on(CoreEvents.PLANNER_DECISION, _record("decision"), model_class=PlannerDecisionPayload)

    batch = [MagicMock(message_id="m1", content="主播好", user=MagicMock(id="u1", name="观众"))]
    assert (await executor.execute(batch, forced=False, trigger_cause=TriggerCause(kind=TriggerKind.BATCH_FULL)))[
        "speech"
    ] == "你好呀"
    await _until(lambda: len(order) >= 4)
    assert order == ["stage", "speech", "decision", "stage"]


async def test_agent_decision_round_completes_with_tts_disabled() -> None:
    agent = make_streamer_agent(tool_registry=ToolRegistry(), speech_config={"enabled": False})
    reply = {"speech": "OK", "emotion": {"name": "happy", "intensity": 0.5}, "actions": [], "metadata": {}}
    agent._planner.plan = AsyncMock(
        return_value=RoundOutcome(
            replied=True,
            target="u1",
            topic_summary="t",
            reply_guidance="r",
            reply_payload=reply,
        )
    )
    replyer = MagicMock()
    replyer.generate = AsyncMock(return_value=reply)
    agent._reply_provider = ReplyToolProvider(replyer=replyer, history_provider=None)

    await agent._rounds.execute(batch=[], forced=False, trigger_cause=TriggerCause(kind=TriggerKind.BATCH_FULL))
    assert agent.get_statistics()["total_replies"] == 1
    assert agent._speech.utterance_queue is None


async def test_agent_wires_speech_dependencies_into_dispatcher() -> None:
    bus, engine, subtitle = EventBus(), _Engine(), MagicMock()
    agent = make_streamer_agent(event_bus=bus, tts_engine=engine, subtitle_service=subtitle, speech_config=_TTS_ON)
    await agent._on_start()
    try:
        speech = agent._speech
        assert (speech._event_bus, speech._tts_engine, speech._subtitle_service) == (bus, engine, subtitle)
        assert speech.tts_enabled is True and speech.utterance_queue is not None
    finally:
        await agent._on_stop()


# ---------------- 嘴的反应通道 ----------------


async def test_reaction_is_dropped_before_any_record_when_the_mouth_stays_busy(started) -> None:
    """前一句迟迟播不完：反应等到上限就整句作废，业务事件不发（记录里不留"说了却没出声"的话）。"""
    bus, engine = EventBus(), _Engine(delay_s=0.5)
    captured = _capture_speech(bus)
    dispatcher = await started(bus=bus, engine=engine, config=_TTS_ON)
    await dispatcher.dispatch({"speech": "一句很长的话", "emotion": {"name": "happy", "intensity": 0.5}})
    await _until(lambda: len(engine.calls) == 1)

    result = await dispatcher.dispatch_reaction(
        "哇！什么东西？！", emotion="scared", emotion_intensity=0.7, wait_ms=100
    )

    assert result is None
    assert [payload.text for _, payload in captured] == ["一句很长的话"]


async def test_reaction_speaks_once_the_mouth_is_free(started) -> None:
    """前一句播完就出声：业务事件、TTS 共用同一 utterance_id，带上情绪。"""
    bus, engine = EventBus(), _Engine(delay_s=0.05)
    captured = _capture_speech(bus)
    dispatcher = await started(bus=bus, engine=engine, config=_TTS_ON)
    await dispatcher.dispatch({"speech": "前一句", "emotion": {"name": "happy", "intensity": 0.5}})

    utterance_id = await dispatcher.dispatch_reaction(
        "疼疼疼！", emotion="scared", emotion_intensity=0.7, wait_ms=2000, gap_ms=10, round_id="mouth_1"
    )

    assert utterance_id is not None
    await _until(lambda: len(engine.calls) == 2)
    assert engine.calls[-1] == ("疼疼疼！", utterance_id)
    payload = captured[-1][1]
    assert (payload.text, payload.emotion, payload.round_id) == ("疼疼疼！", "scared", "mouth_1")


def _spy_emit_busy(dispatcher: SpeechDispatcher) -> dict:
    """记下每句业务事件发出那一刻嘴是不是还占着（排在别人后面出声的台词，发出时嘴是忙的）。"""
    busy_at_emit: dict = {}
    original = dispatcher._emit_streamer_speech

    async def _spy(utterance_id: str, text: str, *args: Any, **kwargs: Any) -> None:
        busy_at_emit[text] = dispatcher.utterance_queue.busy
        await original(utterance_id, text, *args, **kwargs)

    dispatcher._emit_streamer_speech = _spy
    return busy_at_emit


async def test_reaction_gives_up_when_a_planner_line_takes_the_gap(started) -> None:
    """前一句播完时 Planner 的下一句先占了空档：嘴空完再看一眼，剩下的时间等不到就作废，不排到那句后面。"""
    engine = _Engine(delay_s=0.3)
    dispatcher = await started(engine=engine, config=_TTS_ON)
    await dispatcher.dispatch({"speech": "第一句", "emotion": {}})
    planner = asyncio.create_task(dispatcher.dispatch({"speech": "第二句", "emotion": {}}))

    result = await dispatcher.dispatch_reaction(
        "疼疼疼！", emotion="scared", emotion_intensity=0.7, wait_ms=500, gap_ms=100
    )
    await planner

    assert result is None
    await _until(lambda: len(engine.calls) == 2)
    await asyncio.sleep(0.4)
    assert [text for text, _ in engine.calls] == ["第一句", "第二句"]


async def test_reaction_that_lost_the_gap_waits_for_a_free_mouth_instead_of_queueing_behind(started) -> None:
    """空档被 Planner 那句占了但时间还够：等那句播完再出声，发出那一刻嘴是空的。"""
    engine = _Engine(delay_s=0.3)
    dispatcher = await started(engine=engine, config=_TTS_ON)
    busy_at_emit = _spy_emit_busy(dispatcher)
    await dispatcher.dispatch({"speech": "第一句", "emotion": {}})
    planner = asyncio.create_task(dispatcher.dispatch({"speech": "第二句", "emotion": {}}))

    result = await dispatcher.dispatch_reaction(
        "疼疼疼！", emotion="scared", emotion_intensity=0.7, wait_ms=2000, gap_ms=100
    )
    await planner

    assert result is not None
    assert busy_at_emit == {"第一句": False, "第二句": False, "疼疼疼！": False}
    await _until(lambda: len(engine.calls) == 3)
    assert [text for text, _ in engine.calls] == ["第一句", "第二句", "疼疼疼！"]


async def test_planner_line_waits_while_a_reaction_holds_the_gap(started) -> None:
    """嘴已占下空档、还在发出它那句时，Planner 的下一句不能抢先：等嘴那句排进去播完再说。"""
    engine = _Engine(delay_s=0.2)
    dispatcher = await started(engine=engine, config=_TTS_ON)
    original = dispatcher._emit_streamer_speech

    async def _slow_emit(utterance_id: str, text: str, *args: Any, **kwargs: Any) -> None:
        if text == "疼疼疼！":
            # 拉长"占下空档到入队"之间的窗口，让 Planner 那句恰好在这时到
            await asyncio.sleep(0.2)
        await original(utterance_id, text, *args, **kwargs)

    dispatcher._emit_streamer_speech = _slow_emit
    reaction = asyncio.create_task(
        dispatcher.dispatch_reaction("疼疼疼！", emotion="scared", emotion_intensity=0.7, wait_ms=1000)
    )
    await asyncio.sleep(0.05)
    await dispatcher.dispatch({"speech": "接着说", "emotion": {}})

    assert await reaction is not None
    await _until(lambda: len(engine.calls) == 2)
    assert [text for text, _ in engine.calls] == ["疼疼疼！", "接着说"]


@pytest.mark.parametrize("tts_on", [True, False])
async def test_reaction_reports_the_moment_it_starts_playing(started, tts_on: bool) -> None:
    """开始播的回调：TTS 开着时在这句交给 TTS 的那一刻（前一句已播完），关着时在发出的那一刻。"""
    engine = _Engine(delay_s=0.2)
    dispatcher = await started(engine=engine, config=_TTS_ON if tts_on else {"enabled": False})
    await dispatcher.dispatch({"speech": "前一句", "emotion": {}})
    marks: list[int] = []

    await dispatcher.dispatch_reaction(
        "疼疼疼！",
        emotion="scared",
        emotion_intensity=0.7,
        wait_ms=2000,
        on_started=lambda: marks.append(len(engine.calls)),
    )

    if tts_on:
        await _until(lambda: len(engine.calls) == 2)
        assert marks == [1]
    else:
        assert marks == [0]
