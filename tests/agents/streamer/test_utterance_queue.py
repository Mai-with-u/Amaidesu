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
    SpeakCallable,
    UtteranceQueue,
)
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
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
