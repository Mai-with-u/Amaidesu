"""UtteranceQueue：FIFO 串行播放、溢出丢最旧、单条超时跳过、等播背压原语与生命周期"""

from __future__ import annotations

import asyncio
import time
from typing import List, Optional

import pytest

from src.agents.streamer.utterance_queue import (
    DEFAULT_MAX_QUEUE,
    DEFAULT_RENDER_TIMEOUT_MS,
    QUEUE_ENGINE_ID,
    UtteranceQueue,
)
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import UtteranceFailedPayload


class _Recorder:
    """speak 替身：记录调用与最大并发，可人为拖延。"""

    def __init__(self, delay_s: float = 0.0) -> None:
        self.calls: List[tuple[str, Optional[str]]] = []
        self.delay_s = delay_s
        self.inflight = 0
        self.max_inflight = 0

    async def __call__(self, text: str, utterance_id: Optional[str]) -> None:
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        self.calls.append((text, utterance_id))
        try:
            await asyncio.sleep(self.delay_s)
        finally:
            self.inflight -= 1


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"等待条件超时（{timeout}s）")
        await asyncio.sleep(0.01)


@pytest.fixture
async def make_queue():
    queues: list[UtteranceQueue] = []

    async def _make(
        speak,
        max_queue: int = 5,
        render_timeout_ms: int = 2000,
        start: bool = True,
        event_bus: Optional[EventBus] = None,
    ) -> UtteranceQueue:
        queue = UtteranceQueue(
            speak=speak, event_bus=event_bus, max_queue=max_queue, render_timeout_ms=render_timeout_ms
        )
        if start:
            await queue.start()
        queues.append(queue)
        return queue

    yield _make
    for queue in queues:
        await queue.stop()


async def test_processes_fifo_and_serially(make_queue) -> None:
    recorder = _Recorder(delay_s=0.03)
    queue = await make_queue(recorder)
    for i in range(4):
        await queue.enqueue(f"utt_{i}", f"第 {i} 句")
    await _wait_for(lambda: len(recorder.calls) == 4)

    assert recorder.calls == [(f"第 {i} 句", f"utt_{i}") for i in range(4)]
    assert recorder.max_inflight == 1


async def test_overflow_drops_oldest_with_warning(make_queue, loguru_capture) -> None:
    recorder = _Recorder(delay_s=0.2)
    queue = await make_queue(recorder, max_queue=3)
    for uid in ("utt_oldest", "utt_mid", "utt_pre", "utt_overflow"):
        await queue.enqueue(uid, uid)

    assert any(
        r["level"] == "WARNING" and "丢弃最旧 utterance" in r["message"] and "utt_oldest" in r["message"]
        for r in loguru_capture.records
    )
    stats = queue.get_stats()
    assert stats["dropped"] >= 1 and stats["enqueued"] == 4
    await _wait_for(lambda: len(recorder.calls) == 3, timeout=3.0)
    called = [uid for _, uid in recorder.calls]
    assert "utt_oldest" not in called and "utt_overflow" in called


async def test_per_item_timeout_skips_and_continues(make_queue, loguru_capture) -> None:
    calls: list[str] = []

    async def _slow_first(text: str, utterance_id: Optional[str]) -> None:
        calls.append(utterance_id)
        if len(calls) == 1:
            await asyncio.sleep(2.0)

    queue = await make_queue(_slow_first, render_timeout_ms=300)
    await queue.enqueue("utt_slow", "slow")
    await queue.enqueue("utt_fast", "fast")
    await _wait_for(lambda: len(calls) >= 2)

    assert calls == ["utt_slow", "utt_fast"] and queue.get_stats()["timeouts"] == 1
    assert any(
        r["level"] == "ERROR" and "等待超时" in r["message"] and "utt_slow" in r["message"]
        for r in loguru_capture.records
    )


def _capture_failed(bus: EventBus) -> list[UtteranceFailedPayload]:
    """收集 tts.utterance.failed 事件（后台扇出，测试用轮询等待）。"""
    captured: list[UtteranceFailedPayload] = []

    async def _capture(event_name: str, payload: UtteranceFailedPayload, source: str) -> None:
        captured.append(payload)

    bus.on(CoreEvents.TTS_UTTERANCE_FAILED, _capture, model_class=UtteranceFailedPayload)
    return captured


async def test_render_timeout_emits_failed_event(make_queue) -> None:
    """引擎僵死触发渲染看门狗超时：队列代发 tts.utterance.failed，字幕得以兜底出字。"""
    bus = EventBus()
    failed = _capture_failed(bus)

    async def _hang(text: str, utterance_id: Optional[str]) -> None:
        await asyncio.sleep(5.0)

    queue = await make_queue(_hang, render_timeout_ms=150, event_bus=bus)
    await queue.enqueue("utt_hang", "这句卡住了")
    await _wait_for(lambda: len(failed) == 1)

    payload = failed[0]
    assert payload.utterance_id == "utt_hang"
    assert payload.speech_text == "这句卡住了"
    assert payload.engine == QUEUE_ENGINE_ID
    assert "render_timeout" in payload.error_message


async def test_overflow_drop_emits_failed_event(make_queue) -> None:
    """队列满丢弃最旧条目：被丢的这句代发 tts.utterance.failed，字幕不哑。"""
    bus = EventBus()
    failed = _capture_failed(bus)
    recorder = _Recorder(delay_s=0.2)
    queue = await make_queue(recorder, max_queue=1, event_bus=bus)
    await queue.enqueue("utt_first", "第一句")
    await queue.enqueue("utt_second", "第二句")
    await queue.enqueue("utt_third", "第三句")
    await _wait_for(lambda: len(failed) == 2)

    dropped = {payload.utterance_id for payload in failed}
    assert dropped == {"utt_first", "utt_second"}
    assert all(payload.engine == QUEUE_ENGINE_ID for payload in failed)
    assert all("queue_overflow" in payload.error_message for payload in failed)
    await _wait_for(lambda: len(recorder.calls) == 1)
    assert recorder.calls == [("第三句", "utt_third")]


async def test_failed_event_skipped_without_event_bus(make_queue) -> None:
    """未注入事件总线时超时路径只记日志不发布，队列继续运转。"""
    calls: list[str] = []

    async def _slow_first(text: str, utterance_id: Optional[str]) -> None:
        calls.append(utterance_id)
        if len(calls) == 1:
            await asyncio.sleep(2.0)

    queue = await make_queue(_slow_first, render_timeout_ms=150, event_bus=None)
    await queue.enqueue("utt_slow", "slow")
    await queue.enqueue("utt_fast", "fast")
    await _wait_for(lambda: len(calls) >= 2)

    assert calls == ["utt_slow", "utt_fast"] and queue.get_stats()["timeouts"] == 1


async def test_wait_idle_tracks_whole_queue_drain(make_queue) -> None:
    """空闲（未启动或已启动）立即返回；积压时等全部播完才返回 True。"""
    idle = await make_queue(_Recorder(), start=False)
    assert await idle.wait_idle(timeout_ms=100) is True
    await idle.start()
    assert await idle.wait_idle(timeout_ms=100) is True and idle.busy is False

    recorder = _Recorder(delay_s=0.1)
    queue = await make_queue(recorder)
    await queue.enqueue("utt_a", "a")
    await queue.enqueue("utt_b", "b")
    await _wait_for(lambda: queue.busy)
    assert await queue.wait_idle(timeout_ms=2000) is True
    assert len(recorder.calls) == 2 and queue.busy is False


async def test_wait_idle_times_out_while_speaking(make_queue) -> None:
    started = asyncio.Event()

    async def _long_speak(text: str, utterance_id: Optional[str]) -> None:
        started.set()
        await asyncio.sleep(1.0)

    queue = await make_queue(_long_speak, render_timeout_ms=5000)
    await queue.enqueue("utt_a", "text")
    await asyncio.wait_for(started.wait(), 1.0)
    assert await queue.wait_idle(timeout_ms=100) is False and queue.busy is True


async def test_lifecycle_buffer_before_start_idempotent_start_and_reject_after_stop(make_queue) -> None:
    recorder = _Recorder()
    queue = await make_queue(recorder, start=False)
    # 未启动时入队缓冲，启动后消费
    assert await queue.enqueue("utt_pre_start", "text") is True and queue.size == 1
    await queue.start()
    worker = queue._worker_task
    await queue.start()
    assert queue._worker_task is worker
    await _wait_for(lambda: recorder.calls == [("text", "utt_pre_start")])

    # 停止时丢弃未播条目、worker 干净退出，之后的入队被拒
    await queue.enqueue("utt_before_stop", "text")
    await queue.stop()
    assert queue.is_running is False and (queue._worker_task is None or queue._worker_task.done())
    assert await queue.enqueue("utt_after_stop", "text") is False


@pytest.mark.parametrize(
    ("kwargs", "error", "match"),
    [
        ({"speak": "not callable"}, TypeError, "speak"),
        ({"speak": _Recorder(), "max_queue": 0}, ValueError, "max_queue"),
        ({"speak": _Recorder(), "render_timeout_ms": 10}, ValueError, "render_timeout_ms"),
    ],
)
def test_constructor_validation(kwargs: dict, error: type, match: str) -> None:
    with pytest.raises(error, match=match):
        UtteranceQueue(**kwargs)
    # 默认值与核心配置一致
    assert (DEFAULT_MAX_QUEUE, DEFAULT_RENDER_TIMEOUT_MS) == (3, 60_000)


# ---------------- 优先级、过期与段落（快嘴用） ----------------


class _Clock:
    """可拨动的时钟（Unix 毫秒）：判定过期用。"""

    def __init__(self, now: int = 1_000_000) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def _queue_with(recorder: _Recorder, *, max_queue: int = 8, clock: Optional[_Clock] = None) -> UtteranceQueue:
    return UtteranceQueue(speak=recorder, max_queue=max_queue, render_timeout_ms=2000, clock=clock or _Clock())


async def test_higher_priority_jumps_ahead_and_same_priority_stays_fifo() -> None:
    """急的插到前面，同级先来先播：付费致谢、突发反应不必排在闲聊后面。"""
    recorder = _Recorder()
    queue = _queue_with(recorder)
    for uid, priority in (("a", 0), ("b", 0), ("c", 5), ("d", 5), ("e", 1)):
        await queue.enqueue(uid, uid, priority=priority)
    await queue.start()
    try:
        await _wait_for(lambda: len(recorder.calls) == 5)
        assert [uid for _, uid in recorder.calls] == ["c", "d", "e", "a", "b"]
    finally:
        await queue.stop()


async def test_full_queue_drops_the_least_urgent_and_refuses_a_less_urgent_newcomer() -> None:
    """满了丢优先级最低里最旧的；新来的比队里都低时丢新来的，不挤掉更急的。"""
    recorder = _Recorder()
    queue = _queue_with(recorder, max_queue=2)
    assert await queue.enqueue("low", "low", priority=0) is True
    assert await queue.enqueue("high", "high", priority=5) is True
    assert await queue.enqueue("mid", "mid", priority=1) is True, "挤掉最低的 low"
    assert await queue.enqueue("low2", "low2", priority=0) is False, "比队里都低，丢新来的"
    await queue.start()
    try:
        await _wait_for(lambda: len(recorder.calls) == 2)
        assert [uid for _, uid in recorder.calls] == ["high", "mid"]
        assert queue.get_stats()["dropped"] == 2
    finally:
        await queue.stop()


async def test_expired_reaction_is_skipped_when_its_turn_comes() -> None:
    """过了时效的反应轮到时跳过（过时的"哇什么东西"比不说还怪），不过期的照常播。"""
    recorder = _Recorder()
    clock = _Clock()
    queue = _queue_with(recorder, clock=clock)
    await queue.enqueue("reaction", "哇什么东西", ttl_ms=6000)
    await queue.enqueue("chat", "刚才说到哪了")
    clock.now += 7000
    await queue.start()
    try:
        await _wait_for(lambda: len(recorder.calls) == 1)
        await asyncio.sleep(0.05)
        assert [uid for _, uid in recorder.calls] == ["chat"]
        assert queue.get_stats()["expired"] == 1
    finally:
        await queue.stop()


async def test_urgent_line_cuts_a_passage_that_is_already_playing() -> None:
    """长段播到一半被突发反应插进来：剩下的句子作废，不在反应之后接着讲。"""
    recorder = _Recorder(delay_s=0.1)
    queue = _queue_with(recorder)
    await queue.start()
    try:
        for index in range(3):
            await queue.enqueue(f"p{index}", f"第 {index} 句", segment="story")
        await _wait_for(lambda: len(recorder.calls) == 1)
        await queue.enqueue("ouch", "疼疼疼！", priority=5)
        await _wait_for(lambda: len(recorder.calls) == 2)
        await asyncio.sleep(0.25)
        assert [uid for _, uid in recorder.calls] == ["p0", "ouch"]
        assert queue.get_stats()["interrupted"] == 2
    finally:
        await queue.stop()


async def test_passage_not_started_yet_is_not_cut() -> None:
    """段落还没开播时急的先播，段落随后整段照常播。"""
    recorder = _Recorder()
    queue = _queue_with(recorder)
    await queue.enqueue("p0", "第 0 句", segment="story")
    await queue.enqueue("p1", "第 1 句", segment="story")
    await queue.enqueue("ouch", "疼疼疼！", priority=5)
    await queue.start()
    try:
        await _wait_for(lambda: len(recorder.calls) == 3)
        assert [uid for _, uid in recorder.calls] == ["ouch", "p0", "p1"]
        assert queue.get_stats()["interrupted"] == 0
    finally:
        await queue.stop()


async def test_start_callback_fires_when_its_turn_comes_and_never_for_a_skipped_line() -> None:
    """轮到这句、交给 TTS 之前回调一次（嘴按这一刻记延迟）；过期跳过的不回调，回调出错这句照常播。"""
    recorder = _Recorder()
    clock = _Clock()
    queue = _queue_with(recorder, clock=clock)
    started: List[tuple[str, int]] = []

    def _broken() -> None:
        raise RuntimeError("回调坏了")

    await queue.enqueue("stale", "哇什么东西", ttl_ms=6000, on_start=lambda: started.append(("stale", 0)))
    await queue.enqueue("ouch", "疼疼疼！", on_start=lambda: started.append(("ouch", len(recorder.calls))))
    await queue.enqueue("broken", "还好还好", on_start=_broken)
    clock.now += 7000
    await queue.start()
    try:
        await _wait_for(lambda: len(recorder.calls) == 2)
        assert started == [("ouch", 0)], "回调在这句交给 TTS 之前"
        assert [uid for _, uid in recorder.calls] == ["ouch", "broken"]
    finally:
        await queue.stop()
