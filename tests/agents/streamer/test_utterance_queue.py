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
    SPEECH_PRIORITY_AUDIENCE,
    SPEECH_PRIORITY_EMERGENCY,
    SPEECH_PRIORITY_HIGHLIGHT,
    SPEECH_PRIORITY_NORMAL,
    SpeakCallable,
    UtteranceQueue,
)
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.core import CoreErrorPayload
from src.modules.events.payloads.utterance import UtteranceDroppedPayload, UtteranceFailedPayload
from src.modules.tts.common import emit_utterance_failed


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


async def test_unreported_speak_exception_emits_failed_event_and_queue_continues(make_queue, loguru_capture) -> None:
    """speak 抛未报告的异常（如引擎首句懒启动 setup() 失败，一个事件都没发）：队列补发 failed 让字幕出字，并继续下一条。"""
    bus = EventBus()
    failed = _capture_failed(bus)
    calls: list[str] = []

    async def _setup_fails_first(text: str, utterance_id: Optional[str]) -> None:
        calls.append(utterance_id)
        if len(calls) == 1:
            raise RuntimeError("未配置 voice 音色")

    queue = await make_queue(_setup_fails_first, event_bus=bus)
    await queue.enqueue("utt_broken", "这句声音出不来")
    await queue.enqueue("utt_next", "下一句照常")
    assert await queue.wait_idle(timeout_ms=2000)
    await bus.wait_until_drained()

    # 失败的这句补发了 failed 事件，字幕据此照常显示原文；原因带异常类型与信息
    assert len(failed) == 1
    payload = failed[0]
    assert payload.utterance_id == "utt_broken"
    assert payload.speech_text == "这句声音出不来"
    assert payload.engine == QUEUE_ENGINE_ID
    assert payload.error_message.startswith("speak_error: ")
    assert "RuntimeError" in payload.error_message and "未配置 voice 音色" in payload.error_message
    # 队列没被拖垮：下一条照常交给 speak，且只计一次错误
    assert calls == ["utt_broken", "utt_next"]
    assert queue.get_stats()["errors"] == 1
    # 这句只记一条带 utterance_id 的 ERROR
    errors = [r for r in loguru_capture.records if r["level"] == "ERROR" and "speak 调用异常" in r["message"]]
    assert len(errors) == 1 and "utt_broken" in errors[0]["message"]


async def test_exception_already_reported_by_engine_is_counted_but_not_emitted_again(
    make_queue, loguru_capture
) -> None:
    """引擎合成中途失败，先自己发 failed 再抛（异常带"已报告"标记）：队列只计数打日志，不补发第二条。"""
    bus = EventBus()
    failed = _capture_failed(bus)
    calls: list[str] = []

    async def _engine_reports_then_raises(text: str, utterance_id: Optional[str]) -> None:
        calls.append(utterance_id)
        if len(calls) == 1:
            error = ConnectionError("合成中途连接断了")
            # 与真实引擎一致：先发 failed（字幕据此出字）并给异常打标记，再把异常抛出去
            await emit_utterance_failed(
                bus,
                utterance_id=utterance_id,
                speech_text=text,
                engine="fake_engine",
                error_message=f"{type(error).__name__}: {error}",
                cause=error,
            )
            raise error

    queue = await make_queue(_engine_reports_then_raises, event_bus=bus)
    await queue.enqueue("utt_reported", "引擎已经报过失败")
    await queue.enqueue("utt_next", "下一句照常")
    assert await queue.wait_idle(timeout_ms=2000)
    await bus.wait_until_drained()

    # 这句话只有引擎发的那一条 failed，队列没有叠加第二条
    assert [(p.utterance_id, p.engine) for p in failed] == [("utt_reported", "fake_engine")]
    # 计数与日志照旧：错误计一次，ERROR 日志仍有；下一条照常处理
    assert calls == ["utt_reported", "utt_next"]
    assert queue.get_stats()["errors"] == 1
    errors = [r for r in loguru_capture.records if r["level"] == "ERROR" and "speak 调用异常" in r["message"]]
    assert len(errors) == 1 and "utt_reported" in errors[0]["message"]


async def test_speak_exception_without_event_bus_still_continues(make_queue, loguru_capture) -> None:
    """没注入事件总线：speak 抛异常时无处可补发，只计数打日志，队列照常处理下一条。"""
    calls: list[str] = []

    async def _fails_first(text: str, utterance_id: Optional[str]) -> None:
        calls.append(utterance_id)
        if len(calls) == 1:
            raise RuntimeError("未配置 voice 音色")

    queue = await make_queue(_fails_first, event_bus=None)
    await queue.enqueue("utt_broken", "这句声音出不来")
    await queue.enqueue("utt_next", "下一句照常")
    assert await queue.wait_idle(timeout_ms=2000)

    assert calls == ["utt_broken", "utt_next"]
    stats = queue.get_stats()
    assert stats["errors"] == 1 and stats["processed"] == 1
    assert any(r["level"] == "ERROR" and "utt_broken" in r["message"] for r in loguru_capture.records)


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


async def test_interrupted_segment_reports_unplayed_tail_once() -> None:
    """段落被高优台词插断时，作废的后半句合并调一次 on_interrupted 回调。"""
    recorder = _Recorder(delay_s=0.1)
    queue = _queue_with(recorder)
    tails: List[str] = []
    await queue.start()
    try:
        await queue.enqueue("p0", "第一句。", segment="talk_1", on_interrupted=tails.append)
        await queue.enqueue("p1", "第二句！", segment="talk_1", on_interrupted=tails.append)
        await queue.enqueue("p2", "第三句~", segment="talk_1", on_interrupted=tails.append)
        await _wait_for(lambda: len(recorder.calls) == 1)
        cut = await queue.interrupt_segment(min_priority=1)
        assert cut == ["第二句！", "第三句~"]
        assert tails == ["第二句！第三句~"]
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


def _capture_dropped(bus: EventBus) -> list[UtteranceDroppedPayload]:
    captured: list[UtteranceDroppedPayload] = []

    async def _capture(event_name: str, payload: UtteranceDroppedPayload, source: str) -> None:
        captured.append(payload)

    bus.on(CoreEvents.TTS_UTTERANCE_DROPPED, _capture, model_class=UtteranceDroppedPayload)
    return captured


async def test_dropped_events_only_fire_on_four_confirmed_undelivered_paths() -> None:
    """规-01：只有关停清队、停机拒收、段落插断、过期作废发 dropped；溢出、超时、speak 异常只发 failed。"""
    bus = EventBus()
    dropped = _capture_dropped(bus)
    failed = _capture_failed(bus)
    clock = _Clock()
    recorder = _Recorder(delay_s=0.08)
    queue = UtteranceQueue(speak=recorder, event_bus=bus, max_queue=4, render_timeout_ms=2000, clock=clock)
    await queue.start()
    try:
        # 1. 段落被插断：p0 开播后，急事插队丢掉剩余 p1、p2
        await queue.enqueue("p0", "第0句", segment="seg1")
        await queue.enqueue("p1", "第1句", segment="seg1")
        await queue.enqueue("p2", "第2句", segment="seg1")
        await _wait_for(lambda: len(recorder.calls) == 1)
        await queue.enqueue("urgent", "急事", priority=5)
        await _wait_for(lambda: len(recorder.calls) == 2)

        # 2. 过期作废：未启动的新队列先缓冲条目，拨动时钟过时效后再启动消费
        q_exp = UtteranceQueue(speak=recorder, event_bus=bus, max_queue=4, render_timeout_ms=2000, clock=clock)
        await q_exp.enqueue("exp1", "过期反应", ttl_ms=1000)
        await q_exp.enqueue("after_exp", "正常句")
        clock.now += 2000
        await q_exp.start()
        await _wait_for(lambda: any(uid == "after_exp" for _, uid in recorder.calls))
        await q_exp.stop()

        # 3. 关停清队：用慢 speak 堵住当前句，后面排队两句在 stop() 时被清掉
        slow_gate = asyncio.Event()

        async def _block_first(text: str, utterance_id: Optional[str]) -> None:
            if utterance_id == "blocking":
                await slow_gate.wait()

        q2 = UtteranceQueue(speak=_block_first, event_bus=bus, max_queue=2, render_timeout_ms=200, clock=clock)
        await q2.start()
        await q2.enqueue("blocking", "卡住中")
        await asyncio.sleep(0.02)
        # 队列溢出：队满再塞触发 queue_overflow（出字幕，不发 dropped）
        await q2.enqueue("ov_victim", "被挤出")
        await q2.enqueue("queued_stop_1", "没轮到1")
        await q2.enqueue("queued_stop_2", "没轮到2")
        await q2.stop()

        # 4. 停机中入队被拒
        assert await q2.enqueue("rejected_after_stop", "已停机") is False

        # 5. 渲染超时与引擎启动异常：只发 failed，不发 dropped
        async def _fail_or_hang(text: str, utterance_id: Optional[str]) -> None:
            if utterance_id == "hang":
                await asyncio.sleep(5.0)
            raise RuntimeError("引擎未启动")

        q3 = UtteranceQueue(speak=_fail_or_hang, event_bus=bus, max_queue=3, render_timeout_ms=120, clock=clock)
        await q3.start()
        await q3.enqueue("hang", "超时句")
        await q3.enqueue("err", "报错句")
        await q3.wait_idle(timeout_ms=2000)
        await q3.stop()

        await bus.wait_until_drained()
        by_id = {d.utterance_id: d.reason for d in dropped}
        assert by_id == {
            "p1": "segment_interrupted",
            "p2": "segment_interrupted",
            "exp1": "expired",
            "queued_stop_1": "queue_stopped",
            "queued_stop_2": "queue_stopped",
            "rejected_after_stop": "stopping_rejected",
        }
        failed_ids = {f.utterance_id for f in failed}
        assert {"ov_victim", "hang", "err"} <= failed_ids
        assert failed_ids.isdisjoint(set(by_id))
    finally:
        await queue.stop()


# ---------------- 渲染超时熔断（连续超时不逐句空转） ----------------


async def _make_hang_speak(hang_gate: asyncio.Event) -> SpeakCallable:
    """一直卡到门开才返回的 speak 替身：渲染看门狗每句都会超时。"""

    async def _hang(text: str, utterance_id: Optional[str]) -> None:
        await hang_gate.wait()

    return _hang


async def test_fuse_opens_after_consecutive_render_timeouts(loguru_capture) -> None:
    """连续渲染超时达阈值触发熔断：排队句不再等渲染看门狗、立即 failed，且告警一条。"""
    bus = EventBus()
    failed = _capture_failed(bus)
    hang_gate = asyncio.Event()
    queue = UtteranceQueue(
        speak=await _make_hang_speak(hang_gate), event_bus=bus, max_queue=8, render_timeout_ms=150, clock=_Clock()
    )
    await queue.start()
    try:
        await queue.enqueue("hang_1", "超时1")
        await queue.enqueue("hang_2", "超时2")
        await queue.enqueue("hang_3", "超时3")
        # 第 3 句触发熔断；此时再入队两句，验证熔断中排队句快速失败
        await queue.enqueue("queued_1", "排队1")
        await queue.enqueue("queued_2", "排队2")
        await _wait_for(lambda: len(failed) == 5)
        await bus.wait_until_drained()
    finally:
        hang_gate.set()
        await queue.stop()

    by_id = {payload.utterance_id: payload for payload in failed}
    for uid in ("hang_1", "hang_2", "hang_3"):
        assert "render_timeout" in by_id[uid].error_message
        assert "render_timeout_fuse" not in by_id[uid].error_message
    # 熔断后排队句不耗看门狗等待，原因注明熔断
    for uid in ("queued_1", "queued_2"):
        assert "render_timeout_fuse" in by_id[uid].error_message
    # 进入熔断时告警一条（含连续超时句数）
    fuse_logs = [record for record in loguru_capture.records if "触发熔断" in record["message"]]
    assert len(fuse_logs) == 1


async def test_fuse_fails_new_enqueued_utterances_immediately() -> None:
    """熔断期间入队的 utterance 不等渲染看门狗、立即代发 failed。"""
    bus = EventBus()
    failed = _capture_failed(bus)
    hang_gate = asyncio.Event()
    queue = UtteranceQueue(
        speak=await _make_hang_speak(hang_gate), event_bus=bus, max_queue=8, render_timeout_ms=150, clock=_Clock()
    )
    await queue.start()
    try:
        for i in range(3):
            await queue.enqueue(f"hang_{i}", f"超时{i}")
        await _wait_for(lambda: len(failed) == 3)
        await queue.enqueue("during_fuse", "熔断中来一句")
        await _wait_for(lambda: any(payload.utterance_id == "during_fuse" for payload in failed))
        await bus.wait_until_drained()

        payload = next(p for p in failed if p.utterance_id == "during_fuse")
        assert "render_timeout_fuse" in payload.error_message
    finally:
        hang_gate.set()
        await queue.stop()


async def test_fuse_half_open_probe_success_recovers() -> None:
    """熔断满半开间隔放行一句探路：探路成功则恢复正常播报、计数复位。"""
    bus = EventBus()
    failed = _capture_failed(bus)
    calls: List[Optional[str]] = []

    async def _fail_three_then_ok(text: str, utterance_id: Optional[str]) -> None:
        calls.append(utterance_id)
        if len(calls) <= 3:
            await asyncio.sleep(5.0)

    clock = _Clock()
    queue = UtteranceQueue(speak=_fail_three_then_ok, event_bus=bus, max_queue=8, render_timeout_ms=150, clock=clock)
    await queue.start()
    try:
        for i in range(3):
            await queue.enqueue(f"hang_{i}", f"超时{i}")
        await _wait_for(lambda: len(failed) == 3)

        # 未满半开间隔：不放行探路，直接快速失败
        await queue.enqueue("too_early", "间隔未满")
        await _wait_for(lambda: any(p.utterance_id == "too_early" for p in failed))
        assert "render_timeout_fuse" in next(p for p in failed if p.utterance_id == "too_early").error_message

        # 拨过半开间隔：探路句正常渲染成功，熔断关闭，后续句子照常渲染
        clock.now += 60_000
        await queue.enqueue("probe", "探路句")
        await queue.enqueue("after_probe", "恢复后一句")
        await _wait_for(lambda: "after_probe" in calls)
        await bus.wait_until_drained()
        assert not any(p.utterance_id in ("probe", "after_probe") for p in failed)
    finally:
        await queue.stop()


async def test_fuse_probe_failure_keeps_fuse() -> None:
    """半开探路句仍渲染超时：保持熔断并刷新计时，后续句子继续快速失败。"""
    bus = EventBus()
    failed = _capture_failed(bus)
    calls: List[Optional[str]] = []

    async def _always_timeout(text: str, utterance_id: Optional[str]) -> None:
        calls.append(utterance_id)
        await asyncio.sleep(5.0)

    clock = _Clock()
    queue = UtteranceQueue(speak=_always_timeout, event_bus=bus, max_queue=8, render_timeout_ms=150, clock=clock)
    await queue.start()
    try:
        for i in range(3):
            await queue.enqueue(f"hang_{i}", f"超时{i}")
        await _wait_for(lambda: len(failed) == 3)
        await queue.enqueue("queued_during_fuse", "熔断中排队句")
        await _wait_for(lambda: any(p.utterance_id == "queued_during_fuse" for p in failed))

        # 拨过半开间隔放行探路句：探路句仍超时（走 render_timeout 路径），保持熔断
        clock.now += 60_000
        await queue.enqueue("probe_fail", "探路仍超时")
        await _wait_for(lambda: "probe_fail" in calls and len(failed) == 5)
        await queue.enqueue("still_fused", "仍在熔断")
        await _wait_for(lambda: any(p.utterance_id == "still_fused" for p in failed))
        await bus.wait_until_drained()
        assert "render_timeout" in next(p for p in failed if p.utterance_id == "probe_fail").error_message
        assert "render_timeout_fuse" in next(p for p in failed if p.utterance_id == "still_fused").error_message
    finally:
        await queue.stop()


async def test_success_resets_consecutive_timeout_counter() -> None:
    """未熔断时任何一句渲染成功即复位连续计数：2 超时 + 1 成功 + 2 超时不触发熔断。"""
    bus = EventBus()
    failed = _capture_failed(bus)
    calls: List[Optional[str]] = []

    async def _fail_all_but_one(text: str, utterance_id: Optional[str]) -> None:
        calls.append(utterance_id)
        if utterance_id != "ok":
            await asyncio.sleep(5.0)

    queue = UtteranceQueue(speak=_fail_all_but_one, event_bus=bus, max_queue=8, render_timeout_ms=150, clock=_Clock())
    await queue.start()
    try:
        await queue.enqueue("t1", "超时1")
        await queue.enqueue("t2", "超时2")
        await queue.enqueue("ok", "成功句")
        await queue.enqueue("t3", "超时3")
        await queue.enqueue("t4", "超时4")
        await _wait_for(lambda: len(failed) == 4)
        await bus.wait_until_drained()
        # 第 4 次超时只是普通超时失败（连续计数已在前一句成功时复位，远不到阈值），
        # 若熔断已开，t4 会是 fuse 快速失败而非渲染超时
        assert "render_timeout" in next(p for p in failed if p.utterance_id == "t4").error_message
        assert "render_timeout_fuse" not in next(p for p in failed if p.utterance_id == "t4").error_message
    finally:
        await queue.stop()


# ---------------- 熔断触发旁路发布 core.error 告警 ----------------


def _capture_core_error(bus: EventBus) -> list[CoreErrorPayload]:
    """收集 core.error 告警（后台扇出，断言前先 wait_until_drained）。"""
    alerts: list[CoreErrorPayload] = []

    async def _capture(event_name: str, payload: CoreErrorPayload, source: str) -> None:
        alerts.append(payload)

    bus.on(CoreEvents.CORE_ERROR, _capture, model_class=CoreErrorPayload)
    return alerts


async def test_fuse_open_emits_core_error_alert_with_throttle() -> None:
    """熔断触发发一条 core.error（message 写明观众听不到语音与自动恢复）；
    5 分钟窗口内再次熔断不重发，拨过窗口后才发第二条。"""
    bus = EventBus()
    failed = _capture_failed(bus)
    alerts = _capture_core_error(bus)
    calls: List[Optional[str]] = []

    async def _timeout_unless_probe(text: str, utterance_id: Optional[str]) -> None:
        calls.append(utterance_id)
        if utterance_id not in ("probe_1", "probe_2"):
            await asyncio.sleep(5.0)

    clock = _Clock()
    queue = UtteranceQueue(speak=_timeout_unless_probe, event_bus=bus, max_queue=8, render_timeout_ms=150, clock=clock)
    await queue.start()
    try:
        # 第一次熔断：告警一条
        for i in range(3):
            await queue.enqueue(f"t1_{i}", f"超时{i}")
        await _wait_for(lambda: len(failed) == 3)
        await bus.wait_until_drained()
        assert len(alerts) == 1
        message = alerts[0].message or ""
        assert "熔断" in message and "观众听不到语音" in message and "恢复" in message

        # 拨过半开间隔，探路成功恢复 → 再次熔断：距首条告警 < 5 分钟，不重发
        clock.now += 60_000
        await queue.enqueue("probe_1", "探路句")
        await _wait_for(lambda: "probe_1" in calls)
        for i in range(3):
            await queue.enqueue(f"t2_{i}", f"再超时{i}")
        await _wait_for(lambda: len(failed) == 6)
        await bus.wait_until_drained()
        assert len(alerts) == 1

        # 再次恢复后拨过 5 分钟节流窗口 → 第三次熔断重发一条
        clock.now += 60_000
        await queue.enqueue("probe_2", "探路句2")
        await _wait_for(lambda: "probe_2" in calls)
        clock.now += 5 * 60_000
        for i in range(3):
            await queue.enqueue(f"t3_{i}", f"三超时{i}")
        await _wait_for(lambda: len(failed) == 9)
        await bus.wait_until_drained()
        assert len(alerts) == 2
    finally:
        await queue.stop()


# ---------------- 四档优先级与两级打断 ----------------


async def test_speech_priority_constants_order_and_legacy_high_value() -> None:
    """四档优先级常量严格递增，并兼容外部传入更高整数（如旧调用 5）。"""
    assert SPEECH_PRIORITY_NORMAL < SPEECH_PRIORITY_HIGHLIGHT < SPEECH_PRIORITY_AUDIENCE < SPEECH_PRIORITY_EMERGENCY
    assert (
        SPEECH_PRIORITY_NORMAL,
        SPEECH_PRIORITY_HIGHLIGHT,
        SPEECH_PRIORITY_AUDIENCE,
        SPEECH_PRIORITY_EMERGENCY,
    ) == (0, 1, 2, 3)

    recorder = _Recorder()
    queue = _queue_with(recorder, max_queue=8)
    for uid, prio in (
        ("p0", SPEECH_PRIORITY_NORMAL),
        ("p1", SPEECH_PRIORITY_HIGHLIGHT),
        ("p2", SPEECH_PRIORITY_AUDIENCE),
        ("p3", SPEECH_PRIORITY_EMERGENCY),
        ("legacy5", 5),
    ):
        await queue.enqueue(uid, uid, priority=prio)
    await queue.start()
    try:
        assert await queue.wait_idle(timeout_ms=2000) is True
        assert [uid for _, uid in recorder.calls] == ["legacy5", "p3", "p2", "p1", "p0"]
    finally:
        await queue.stop()


async def test_interrupt_lower_priority_clears_same_segment_and_other_low_pending_with_tail_callback() -> None:
    """一级打断：清同段余句（segment_interrupted + 尾句回调）与跨段低优待播（priority_preempted），保在播句与同级/高优待播。"""
    bus = EventBus()
    dropped = _capture_dropped(bus)
    playing_started = asyncio.Event()
    release_playing = asyncio.Event()
    played: List[str] = []

    async def _gated_speak(text: str, utterance_id: Optional[str]) -> None:
        played.append(utterance_id or "")
        if utterance_id == "seg_0":
            playing_started.set()
            await release_playing.wait()

    queue = UtteranceQueue(speak=_gated_speak, event_bus=bus, max_queue=8, render_timeout_ms=2000, clock=_Clock())
    tails: List[str] = []
    await queue.start()
    try:
        # 正在播的多句段落（P1）+ 同段待播两句；再塞入 P0 常规独白与同级 P1 独立句（不会在入队期误切当前段落）
        await queue.enqueue(
            "seg_0", "段一。", priority=SPEECH_PRIORITY_HIGHLIGHT, segment="talk_1", on_interrupted=tails.append
        )
        await queue.enqueue(
            "seg_1", "段二。", priority=SPEECH_PRIORITY_HIGHLIGHT, segment="talk_1", on_interrupted=tails.append
        )
        await queue.enqueue(
            "seg_2", "段三。", priority=SPEECH_PRIORITY_HIGHLIGHT, segment="talk_1", on_interrupted=tails.append
        )
        await asyncio.wait_for(playing_started.wait(), timeout=2.0)

        await queue.enqueue("mono_p0", "常规解说", priority=SPEECH_PRIORITY_NORMAL)
        await queue.enqueue("hi_p1", "高光反应", priority=SPEECH_PRIORITY_HIGHLIGHT)

        # min_priority <= 0 不做清理
        assert await queue.interrupt_lower_priority(SPEECH_PRIORITY_NORMAL) == []

        # P2 一级打断：同段余句 seg_1/seg_2 走 segment_interrupted + 尾句回调，跨段低优 mono_p0/hi_p1 走 priority_preempted
        removed = await queue.interrupt_lower_priority(SPEECH_PRIORITY_AUDIENCE)
        assert set(removed) == {"段二。", "段三。", "常规解说", "高光反应"}
        assert tails == ["段二。段三。"]
        assert queue.size == 0

        # 再验证同级/高优待播不会被清：同时入队 P2 观众回复与 P0 独白，再次 P2 一级打断只清 P0、保留 P2
        await queue.enqueue("aud_p2", "观众回复", priority=SPEECH_PRIORITY_AUDIENCE)
        await queue.enqueue("mono_p0_2", "补充解说", priority=SPEECH_PRIORITY_NORMAL)
        removed_second = await queue.interrupt_lower_priority(SPEECH_PRIORITY_AUDIENCE)
        assert removed_second == ["补充解说"]
        assert queue.size == 1

        # 放行正在播的 seg_0，随后只剩同级 aud_p2 播出
        release_playing.set()
        assert await queue.wait_idle(timeout_ms=2000) is True
        await bus.wait_until_drained()

        assert played == ["seg_0", "aud_p2"]
        by_id = {d.utterance_id: d.reason for d in dropped}
        assert by_id == {
            "seg_1": "segment_interrupted",
            "seg_2": "segment_interrupted",
            "mono_p0": "priority_preempted",
            "hi_p1": "priority_preempted",
            "mono_p0_2": "priority_preempted",
        }
        assert queue.get_stats()["interrupted"] == 5
    finally:
        release_playing.set()
        await queue.stop()


async def test_interrupt_segment_remains_backward_compatible_for_current_segment_only() -> None:
    """interrupt_segment 保持向下兼容：只收当前在播段落余句，不清理其它独立待播句子。"""
    bus = EventBus()
    dropped = _capture_dropped(bus)
    playing_started = asyncio.Event()
    release_playing = asyncio.Event()
    played: List[str] = []

    async def _gated_speak(text: str, utterance_id: Optional[str]) -> None:
        played.append(utterance_id or "")
        if utterance_id == "seg_0":
            playing_started.set()
            await release_playing.wait()

    queue = UtteranceQueue(speak=_gated_speak, event_bus=bus, max_queue=6, render_timeout_ms=2000, clock=_Clock())
    await queue.start()
    try:
        await queue.enqueue("seg_0", "段一。", priority=SPEECH_PRIORITY_NORMAL, segment="talk_1")
        await queue.enqueue("seg_1", "段二。", priority=SPEECH_PRIORITY_NORMAL, segment="talk_1")
        await asyncio.wait_for(playing_started.wait(), timeout=2.0)
        await queue.enqueue("solo_p0", "独立句", priority=SPEECH_PRIORITY_NORMAL)

        cut = await queue.interrupt_segment(SPEECH_PRIORITY_AUDIENCE)
        assert cut == ["段二。"]
        release_playing.set()
        assert await queue.wait_idle(timeout_ms=2000) is True
        await bus.wait_until_drained()

        assert played == ["seg_0", "solo_p0"]
        assert [(d.utterance_id, d.reason) for d in dropped] == [("seg_1", "segment_interrupted")]
    finally:
        release_playing.set()
        await queue.stop()


async def test_hard_interrupt_playing_cancels_p0_emits_hard_interrupted_and_keeps_worker_alive() -> None:
    """二级硬打断：掐断在播 P0 并发 hard_interrupted、顺带清低优待播，不发 failed/不计超时，worker 存活并立刻播紧急发言。"""
    bus = EventBus()
    dropped = _capture_dropped(bus)
    failed = _capture_failed(bus)
    playing_started = asyncio.Event()
    playing_cancelled = asyncio.Event()
    emergency_done = asyncio.Event()
    played: List[str] = []

    async def _speak(text: str, utterance_id: Optional[str]) -> None:
        played.append(utterance_id or "")
        if utterance_id == "p0_playing":
            playing_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                playing_cancelled.set()
                raise
        if utterance_id == "emergency_1":
            emergency_done.set()

    queue = UtteranceQueue(speak=_speak, event_bus=bus, max_queue=6, render_timeout_ms=2000, clock=_Clock())
    tails: List[str] = []
    await queue.start()
    try:
        await queue.enqueue(
            "p0_playing",
            "漫长独白第一句。",
            priority=SPEECH_PRIORITY_NORMAL,
            segment="talk_seg",
            on_interrupted=tails.append,
        )
        await queue.enqueue(
            "p0_tail",
            "漫长独白第二句。",
            priority=SPEECH_PRIORITY_NORMAL,
            segment="talk_seg",
            on_interrupted=tails.append,
        )
        await asyncio.wait_for(playing_started.wait(), timeout=2.0)
        await queue.enqueue("p0_other", "排队中的普通解说", priority=SPEECH_PRIORITY_NORMAL)

        # 触发二级硬打断并紧接着入队极急发言
        assert await queue.hard_interrupt_playing(SPEECH_PRIORITY_EMERGENCY) is True
        await asyncio.wait_for(playing_cancelled.wait(), timeout=2.0)
        assert await queue.enqueue("emergency_1", "救命掉岩浆了！", priority=SPEECH_PRIORITY_EMERGENCY) is True
        await asyncio.wait_for(emergency_done.wait(), timeout=2.0)
        assert await queue.wait_idle(timeout_ms=2000) is True
        await bus.wait_until_drained()

        assert queue.is_running is True
        assert played == ["p0_playing", "emergency_1"]
        assert tails == ["漫长独白第二句。"]
        assert failed == []
        by_id = {d.utterance_id: d.reason for d in dropped}
        assert by_id == {
            "p0_tail": "segment_interrupted",
            "p0_other": "priority_preempted",
            "p0_playing": "hard_interrupted",
        }
        stats = queue.get_stats()
        assert stats["timeouts"] == 0
        assert stats["errors"] == 0
        assert stats["interrupted"] == 3
        assert stats["processed"] == 1
    finally:
        await queue.stop()


@pytest.mark.parametrize(
    "victim_priority",
    [SPEECH_PRIORITY_HIGHLIGHT, SPEECH_PRIORITY_AUDIENCE, SPEECH_PRIORITY_EMERGENCY],
    ids=["p1_highlight", "p2_audience", "p3_emergency"],
)
async def test_hard_interrupt_refuses_to_cut_p1_and_higher_playing(victim_priority: int) -> None:
    """二级硬打断默认只掐 P0，绝对不硬打断正在播放的 P1 高光、P2 观众回复或 P3 极急发言。"""
    bus = EventBus()
    dropped = _capture_dropped(bus)
    playing_started = asyncio.Event()
    release_playing = asyncio.Event()
    was_cancelled = False

    async def _speak(text: str, utterance_id: Optional[str]) -> None:
        nonlocal was_cancelled
        playing_started.set()
        try:
            await release_playing.wait()
        except asyncio.CancelledError:
            was_cancelled = True
            raise

    queue = UtteranceQueue(speak=_speak, event_bus=bus, max_queue=4, render_timeout_ms=2000, clock=_Clock())
    await queue.start()
    try:
        # 空闲无在播条目时返回 False
        assert await queue.hard_interrupt_playing(SPEECH_PRIORITY_EMERGENCY) is False

        await queue.enqueue("protected", "重要发言", priority=victim_priority)
        await asyncio.wait_for(playing_started.wait(), timeout=2.0)

        assert await queue.hard_interrupt_playing(SPEECH_PRIORITY_EMERGENCY) is False
        release_playing.set()
        assert await queue.wait_idle(timeout_ms=2000) is True
        await bus.wait_until_drained()

        assert was_cancelled is False
        assert dropped == []
        assert queue.get_stats()["processed"] == 1
        assert queue.get_stats()["interrupted"] == 0
    finally:
        release_playing.set()
        await queue.stop()


async def test_hard_interrupt_requires_emergency_min_priority() -> None:
    """低于 SPEECH_PRIORITY_EMERGENCY 的调用即使面对在播 P0 也不得触发二级硬打断。"""
    playing_started = asyncio.Event()
    release_playing = asyncio.Event()
    was_cancelled = False

    async def _speak(text: str, utterance_id: Optional[str]) -> None:
        nonlocal was_cancelled
        playing_started.set()
        try:
            await release_playing.wait()
        except asyncio.CancelledError:
            was_cancelled = True
            raise

    queue = UtteranceQueue(speak=_speak, max_queue=4, render_timeout_ms=2000, clock=_Clock())
    await queue.start()
    try:
        await queue.enqueue("p0_item", "常规解说", priority=SPEECH_PRIORITY_NORMAL)
        await asyncio.wait_for(playing_started.wait(), timeout=2.0)
        assert await queue.hard_interrupt_playing(SPEECH_PRIORITY_AUDIENCE) is False
        release_playing.set()
        assert await queue.wait_idle(timeout_ms=2000) is True
        assert was_cancelled is False
        assert queue.get_stats()["processed"] == 1
    finally:
        release_playing.set()
        await queue.stop()


async def test_hard_interrupt_render_timeout_and_stop_are_strictly_isolated() -> None:
    """硬打断、渲染超时、队列关停三路 CancelledError/Timeout 互不串扰。"""
    bus = EventBus()
    dropped = _capture_dropped(bus)
    failed = _capture_failed(bus)

    p0_started = asyncio.Event()
    stop_target_started = asyncio.Event()
    stop_target_cancelled = asyncio.Event()
    after_timeout_done = asyncio.Event()

    async def _speak(text: str, utterance_id: Optional[str]) -> None:
        if utterance_id == "p0_cut":
            p0_started.set()
            await asyncio.Event().wait()
        elif utterance_id == "hang_timeout":
            await asyncio.Event().wait()
        elif utterance_id == "after_timeout":
            after_timeout_done.set()
        elif utterance_id == "stop_victim":
            stop_target_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                stop_target_cancelled.set()
                raise

    queue = UtteranceQueue(speak=_speak, event_bus=bus, max_queue=6, render_timeout_ms=120, clock=_Clock())
    await queue.start()
    try:
        # 1) 硬打断：走 hard_interrupted，不计 timeouts、不发 failed
        await queue.enqueue("p0_cut", "要被掐断的独白", priority=SPEECH_PRIORITY_NORMAL)
        await asyncio.wait_for(p0_started.wait(), timeout=2.0)
        assert await queue.hard_interrupt_playing(SPEECH_PRIORITY_EMERGENCY) is True
        assert await queue.wait_idle(timeout_ms=2000) is True
        assert queue.get_stats()["timeouts"] == 0

        # 2) 渲染超时：仍按原逻辑计 timeouts + 发 failed，随后 worker 照常播下一句
        await queue.enqueue("hang_timeout", "卡死的句子", priority=SPEECH_PRIORITY_NORMAL)
        await queue.enqueue("after_timeout", "超时后的正常句", priority=SPEECH_PRIORITY_NORMAL)
        await asyncio.wait_for(after_timeout_done.wait(), timeout=2.0)
        assert await queue.wait_idle(timeout_ms=2000) is True
        assert queue.get_stats()["timeouts"] == 1

        # 3) 队列关停：取消在播条目并干净退出 worker，不发 hard_interrupted 也不发 failed；排队条目发 queue_stopped
        await queue.enqueue("stop_victim", "关停时在播", priority=SPEECH_PRIORITY_NORMAL)
        await asyncio.wait_for(stop_target_started.wait(), timeout=2.0)
        await queue.enqueue("queued_at_stop", "关停时在排", priority=SPEECH_PRIORITY_NORMAL)
        await queue.stop()
        await asyncio.wait_for(stop_target_cancelled.wait(), timeout=2.0)
        await bus.wait_until_drained()

        assert queue.is_running is False
        dropped_map = {d.utterance_id: d.reason for d in dropped}
        assert dropped_map == {
            "p0_cut": "hard_interrupted",
            "queued_at_stop": "queue_stopped",
        }
        assert [f.utterance_id for f in failed] == ["hang_timeout"]
        assert "render_timeout" in failed[0].error_message
    finally:
        await queue.stop()
