"""UtteranceQueue：FIFO 串行播放、溢出丢最旧、单条超时跳过、等播背压原语与生命周期"""

from __future__ import annotations

import asyncio
import time
from typing import List, Optional

import pytest

from src.agents.streamer.utterance_queue import DEFAULT_MAX_QUEUE, DEFAULT_RENDER_TIMEOUT_MS, UtteranceQueue


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

    async def _make(speak, max_queue: int = 5, render_timeout_ms: int = 2000, start: bool = True) -> UtteranceQueue:
        queue = UtteranceQueue(speak=speak, max_queue=max_queue, render_timeout_ms=render_timeout_ms)
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
