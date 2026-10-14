"""SpeechDispatcher 单元测试：派发扇出 + TTS 队列生命周期降级。

Agent 级端到端行为（事件形状 / 序号 / 降级）由 ``test_streamer_agent_wiring``
等覆盖；本文件直接打 dispatcher，锁定组件级契约：
- dispatch 消费 reply 结构化结果：业务事件 / 字幕 / TTS 入队 / 返回三元组
  （情绪渲染走适配器订阅 streamer.speech 的自动路径，不在本管线扇出）
- start：启用但无引擎 → 降级关闭不抛；stop 幂等
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

from src.agents.streamer.speech_dispatcher import SpeechDispatcher
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload


def _make_dispatcher(
    event_bus=None,
    subtitle_service=None,
    tts_engine=None,
    speech_config=None,
) -> SpeechDispatcher:
    return SpeechDispatcher(
        event_bus=event_bus,
        subtitle_service=subtitle_service,
        tts_engine=tts_engine,
        speech_config=speech_config,
    )


# dispatch 扇出


async def test_dispatch_fans_out_to_all_downstreams():
    """speech+emotion 齐备：业务事件 / 字幕各路到位，返回三元组。"""
    bus = EventBus()
    captured: list[StreamerSpeechPayload] = []
    captured_event = asyncio.Event()

    async def _capture(event_name, payload, source=None):
        if isinstance(payload, StreamerSpeechPayload):
            captured.append(payload)
            captured_event.set()

    bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)

    subtitle = MagicMock()
    subtitle.show = AsyncMock()

    dispatcher = _make_dispatcher(
        event_bus=bus,
        subtitle_service=subtitle,
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    # 无 TTS 引擎 → start 降级关闭；业务事件与字幕不依赖队列启用
    await dispatcher.start()

    result = await dispatcher.dispatch(
        {
            "speech": "你好",
            "emotion": {"name": "happy", "intensity": 0.8},
        },
        target_user_id="u1",
        reply_to_message_id="m9",
        round_id="rnd_1",
    )
    await asyncio.wait_for(captured_event.wait(), timeout=2.0)
    await asyncio.sleep(0)  # 让字幕任务得到调度

    speech, emotion, utterance_id = result
    assert speech == "你好"
    assert emotion == "happy"
    assert utterance_id and utterance_id.startswith("utt_")

    # 业务事件：关联键齐全 + 情绪事实必有值
    assert len(captured) == 1
    payload = captured[0]
    assert payload.text == "你好"
    assert payload.target_user_id == "u1"
    assert payload.reply_to_message_id == "m9"
    assert payload.round_id == "rnd_1"
    assert payload.utterance_id == utterance_id
    assert payload.emotion == "happy"
    assert payload.emotion_intensity == 0.8

    # 字幕
    subtitle.show.assert_awaited_once()
    assert subtitle.show.await_args.args[0] == "你好"


async def test_dispatch_non_dict_payload_returns_none():
    dispatcher = _make_dispatcher()
    assert await dispatcher.dispatch(["speech", "emotion"]) is None


# TTS 队列生命周期：降级与幂等


async def test_start_degrades_when_enabled_but_no_engine():
    """启用但无引擎：start 不抛、TTS 关闭、speech 业务事件照发。"""
    bus = EventBus()
    captured: list[StreamerSpeechPayload] = []
    captured_event = asyncio.Event()

    async def _capture(event_name, payload, source=None):
        captured.append(payload)
        captured_event.set()

    bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)

    dispatcher = _make_dispatcher(
        event_bus=bus,
        tts_engine=None,
        speech_config={"enabled": True},
    )
    await dispatcher.start()
    assert dispatcher.tts_enabled is False
    assert dispatcher.utterance_queue is None

    result = await dispatcher.dispatch({"speech": "仍在说话", "emotion": {"name": "neutral", "intensity": 0.5}})
    await asyncio.wait_for(captured_event.wait(), timeout=2.0)
    assert result is not None and result[0] == "仍在说话"

    await dispatcher.stop()  # 降级态 stop 安全


async def test_stop_is_idempotent_and_stops_queue():
    """stop 后队列置空；重复 stop 不报错。"""
    engine = MagicMock()
    engine.handle_speech = AsyncMock()
    dispatcher = _make_dispatcher(
        tts_engine=engine,
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await dispatcher.start()
    queue = dispatcher.utterance_queue
    assert queue is not None

    await dispatcher.stop()
    assert dispatcher.utterance_queue is None
    await dispatcher.stop()  # 幂等


async def test_disabled_config_never_builds_queue():
    """未启用（默认）：start 是 no-op，不构造队列。"""
    dispatcher = _make_dispatcher(tts_engine=MagicMock())
    await dispatcher.start()
    assert dispatcher.tts_enabled is False
    assert dispatcher.utterance_queue is None


# 发言等播放（背压闸）


class _PlaybackEngine:
    """慢播放 TTS 引擎替身：handle_speech 拖延 delay_s 模拟"合成+播放"全周期。"""

    def __init__(self, delay_s: float = 0.0) -> None:
        self.calls: list[tuple[str, Optional[str]]] = []
        self.delay_s = delay_s

    async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
        self.calls.append((text, utterance_id))
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)


def _speech_config(wait_playback: bool, wait_timeout_ms: int = 5000) -> dict:
    return {
        "enabled": True,
        "max_queue": 3,
        "render_timeout_ms": 5000,
        "wait_playback": wait_playback,
        "wait_playback_timeout_ms": wait_timeout_ms,
    }


async def _capture_emit_times(bus: EventBus) -> list[float]:
    """订阅 streamer.speech 并按发出时刻记录，返回时刻列表（原地填充）。"""
    emit_times: list[float] = []

    async def _capture(event_name, payload, source=None):
        if isinstance(payload, StreamerSpeechPayload):
            emit_times.append(time.monotonic())

    bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)
    return emit_times


async def test_dispatch_waits_for_playback_before_emit():
    """wait_playback 开启：第二句的业务事件发出要等第一句播完（嘴空才放行）。"""
    bus = EventBus()
    emit_times = await _capture_emit_times(bus)
    engine = _PlaybackEngine(delay_s=0.4)
    dispatcher = _make_dispatcher(
        event_bus=bus,
        tts_engine=engine,
        speech_config=_speech_config(wait_playback=True),
    )
    await dispatcher.start()
    try:
        await dispatcher.dispatch({"speech": "第一句", "emotion": {}})
        await dispatcher.dispatch({"speech": "第二句", "emotion": {}})

        # EventBus.emit 对 handler 是后台扇出：等两条事件都送达再断言时序
        await asyncio.wait_for(_all_spoken(engine, 2), timeout=2.0)
        await _wait_emitted(emit_times, 2)
        gap = emit_times[1] - emit_times[0]
        assert gap >= 0.3, f"第二句应等第一句播完（0.4s）后才发出，实际间隔 {gap:.3f}s"
        # 两条都已入 TTS 队列并按序播放
        assert [text for text, _uid in engine.calls] == ["第一句", "第二句"]
    finally:
        await dispatcher.stop()


async def _all_spoken(engine: _PlaybackEngine, count: int) -> None:
    """轮询等待引擎收满 count 次 speak 调用。"""
    while len(engine.calls) < count:
        await asyncio.sleep(0.01)


async def _wait_emitted(emit_times: list[float], count: int, timeout_s: float = 2.0) -> None:
    """轮询等待事件 handler 收满 count 条（EventBus.emit 对 handler 是后台扇出）。"""
    deadline = time.monotonic() + timeout_s
    while len(emit_times) < count:
        if time.monotonic() > deadline:
            raise AssertionError(f"等待 {count} 条 streamer.speech 超时，实际收到 {len(emit_times)} 条")
        await asyncio.sleep(0.01)


async def test_dispatch_no_wait_when_wait_playback_disabled():
    """wait_playback 关闭：连续 dispatch 立即返回（fire-and-forget 语义）。"""
    bus = EventBus()
    emit_times = await _capture_emit_times(bus)
    engine = _PlaybackEngine(delay_s=0.4)
    dispatcher = _make_dispatcher(
        event_bus=bus,
        tts_engine=engine,
        speech_config=_speech_config(wait_playback=False),
    )
    await dispatcher.start()
    try:
        t0 = time.monotonic()
        await dispatcher.dispatch({"speech": "第一句", "emotion": {}})
        await dispatcher.dispatch({"speech": "第二句", "emotion": {}})
        elapsed = time.monotonic() - t0

        assert elapsed < 0.3, f"关闭等播时两次 dispatch 不应阻塞，实际 {elapsed:.3f}s"
        await asyncio.wait_for(_all_spoken(engine, 2), timeout=2.0)
        await _wait_emitted(emit_times, 2)
        assert emit_times[1] - emit_times[0] < 0.3
    finally:
        await dispatcher.stop()


async def test_dispatch_fails_open_on_wait_timeout():
    """等播超时 fail-open：放行发言不丢话，不无限阻塞决策循环。"""
    bus = EventBus()
    emit_times = await _capture_emit_times(bus)
    engine = _PlaybackEngine(delay_s=1.0)
    dispatcher = _make_dispatcher(
        event_bus=bus,
        tts_engine=engine,
        speech_config=_speech_config(wait_playback=True, wait_timeout_ms=150),
    )
    await dispatcher.start()
    try:
        await dispatcher.dispatch({"speech": "第一句", "emotion": {}})
        t0 = time.monotonic()
        result = await dispatcher.dispatch({"speech": "第二句", "emotion": {}})
        elapsed = time.monotonic() - t0

        # 等了约 150ms 超时放行，没等满 1s 的播放
        assert result is not None and result[0] == "第二句"
        assert 0.1 <= elapsed < 0.9, f"应在超时后放行，实际等待 {elapsed:.3f}s"
        await _wait_emitted(emit_times, 2)
    finally:
        await dispatcher.stop()


# 异步扇出持有 + 停止汇合（P1-2）


async def test_stop_drains_inflight_fanout_without_pending_warnings():
    """dispatch 后立即 stop：在飞扇出任务被汇合，无 "Task was destroyed but it is pending"。

    覆盖：业务事件 emit / 字幕 show 两路。汇合点为 ``SpeechDispatcher.stop()``，
    限 2 秒；超时不抛（本用例不构造超时场景）。
    """
    bus = EventBus()
    captured_event = asyncio.Event()

    async def _capture(event_name, payload, source=None):
        if isinstance(payload, StreamerSpeechPayload):
            captured_event.set()

    bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)

    subtitle = MagicMock()
    subtitle.show = AsyncMock()
    engine = MagicMock()
    engine.handle_speech = AsyncMock()

    dispatcher = _make_dispatcher(
        event_bus=bus,
        subtitle_service=subtitle,
        tts_engine=engine,
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await dispatcher.start()
    assert dispatcher.tts_enabled is True

    # 各路扇出（speech/emotion/业务事件/字幕）一次性触发，立即 stop 模拟"决策循环立刻回收"
    await dispatcher.dispatch(
        {
            "speech": "你好",
            "emotion": {"name": "happy", "intensity": 0.8},
        },
        round_id="rnd_drain",
    )
    await dispatcher.stop()

    # 汇合完成意味着"无悬挂任务"——所有 task 必须 done；callback 的 discard
    # 由 call_soon 排队，sleep(0) 让其执行一次；再断言集合清空。
    assert all(t.done() for t in dispatcher._bg_tasks), (
        f"stop 后仍有未完成的后台任务: {[t for t in dispatcher._bg_tasks if not t.done()]}"
    )
    await asyncio.sleep(0)
    assert dispatcher._bg_tasks == set()


async def test_dispatch_returns_synchronously_without_awaiting_slow_invoke():
    """决策循环安全：dispatch 在慢 TTS 入队面前仍同步返回（fire-and-forget 语义保留）。

    注入一个 1.0s 慢 handle_speech；dispatch 必须立即返回；stop 在 2 秒汇合窗口内完成。
    """
    sleep_done = asyncio.Event()
    speak_started = asyncio.Event()

    async def _slow_speak(text, utterance_id=None):
        speak_started.set()
        await asyncio.sleep(1.0)
        sleep_done.set()

    engine = MagicMock()
    engine.handle_speech = AsyncMock(side_effect=_slow_speak)

    dispatcher = _make_dispatcher(
        tts_engine=engine,
        speech_config={"enabled": True},
    )
    await dispatcher.start()

    t0 = time.monotonic()
    result = await dispatcher.dispatch({"speech": "x", "emotion": {"name": "happy", "intensity": 0.8}})
    elapsed = time.monotonic() - t0

    # dispatch 必须几乎瞬时返回（远小于 1s 的 sleep）；扇出慢路径不阻塞决策循环
    assert elapsed < 0.1, f"dispatch 不应阻塞扇出，但耗时 {elapsed:.3f}s"
    assert result is not None and result[0] == "x"

    await asyncio.wait_for(speak_started.wait(), timeout=1.0)

    t1 = time.monotonic()
    await dispatcher.stop()
    stop_elapsed = time.monotonic() - t1
    # 1.0s 慢 speak + 余量，stop 必须在 2 秒汇合窗口内完成
    assert stop_elapsed < 2.5, f"stop 应在 2 秒超时内完成，但耗时 {stop_elapsed:.3f}s"

    await asyncio.sleep(0)
    assert dispatcher._bg_tasks == set()


async def test_stop_is_bounded_when_speak_hangs_longer_than_timeout():
    """汇合有界：speak 永远不结束时 stop 仍及时返回、不抛。

    队列 worker 的挂死调用由 ``utterance_queue.stop()`` 的取消机制截断
    （强于后台汇合窗口）；stop 需在合理时间内干净返回。
    """
    started = asyncio.Event()

    async def _hanging_speak(text, utterance_id=None):
        started.set()
        # 远超 2 秒 timeout；验证队列取消机制兜住挂死路径
        await asyncio.sleep(60)

    engine = MagicMock()
    engine.handle_speech = AsyncMock(side_effect=_hanging_speak)

    dispatcher = _make_dispatcher(
        tts_engine=engine,
        speech_config={"enabled": True},
    )
    await dispatcher.start()

    await dispatcher.dispatch({"speech": "y", "emotion": {"name": "happy", "intensity": 0.5}})
    await asyncio.wait_for(started.wait(), timeout=1.0)

    t0 = time.monotonic()
    await dispatcher.stop()
    elapsed = time.monotonic() - t0
    assert elapsed < 3.0, f"stop 应及时返回，实际 {elapsed:.3f}s"
    assert dispatcher.utterance_queue is None
