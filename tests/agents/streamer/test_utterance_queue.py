"""UtteranceQueue 单元测试。

覆盖需求：
- FIFO 顺序
-  drop-oldest on overflow
- 串行执行
- 单 utterance 超时跳过
- 干净停止
- 禁用（未 start）= no-op（enqueue 不报错，但无 speak）
- 构造参数校验

全部使用 mock speak callable：不依赖真实 TTS / EventBus / LLM。
"""

from __future__ import annotations

import asyncio
import time
from typing import List, Optional
from unittest.mock import AsyncMock

import pytest

from src.agents.streamer.utterance_queue import (
    DEFAULT_MAX_QUEUE,
    DEFAULT_RENDER_TIMEOUT_MS,
    SpeakCallable,
    UtteranceQueue,
)


# ---------------------------------------------------------------------------
# 测试辅助：可记录调用顺序的 mock speak callable
# ---------------------------------------------------------------------------


class _MockSpeakRecorder:
    """录制所有 speak 调用，并允许人为延迟与失败模拟。

    实例本身即 ``SpeakCallable``（``await recorder(text, uid)``）。
    """

    def __init__(
        self,
        *,
        delay_s: float = 0.0,
        fail_with: Optional[Exception] = None,
    ) -> None:
        self.calls: List[tuple[str, Optional[str]]] = []
        self.delay_s = delay_s
        self.fail_with = fail_with
        self._inflight = 0
        self._max_inflight = 0
        self._lock = asyncio.Lock()
        self.completed = asyncio.Event()

    async def __call__(self, text: str, utterance_id: Optional[str]) -> None:
        async with self._lock:
            self._inflight += 1
            self._max_inflight = max(self._max_inflight, self._inflight)
        self.calls.append((text, utterance_id))
        try:
            if self.delay_s > 0:
                await asyncio.sleep(self.delay_s)
            if self.fail_with is not None:
                raise self.fail_with
        finally:
            async with self._lock:
                self._inflight -= 1
                if not self._inflight:
                    self.completed.set()


# ---------------------------------------------------------------------------
# loguru 日志捕获（与 conftest 中的 helper 复制）
# ---------------------------------------------------------------------------


from loguru import logger as _loguru_logger  # noqa: E402


class _LoguruCapture:
    """内存里捕获 loguru 日志记录（项目用 loguru，pytest caplog 不适用）。

    每个测试用 fixture 实例化一次；测试结束后清理 sink。
    """

    def __init__(self) -> None:
        self.records: List[dict] = []
        self._sink_id: Optional[int] = None

    def __enter__(self) -> "_LoguruCapture":
        def _sink(message) -> None:
            record = message.record
            self.records.append(
                {
                    "level": record["level"].name,
                    "message": record["message"],
                    "module": record["name"],
                }
            )

        self._sink_id = _loguru_logger.add(_sink, level="DEBUG")
        return self

    def __exit__(self, *exc_info) -> None:
        if self._sink_id is not None:
            _loguru_logger.remove(self._sink_id)
            self._sink_id = None


@pytest.fixture
def loguru_capture():
    """提供 _LoguruCapture 实例，自动管理 sink 生命周期。"""
    cap = _LoguruCapture()
    with cap:
        yield cap


# ---------------------------------------------------------------------------
# FIFO / 串行
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_processes_in_fifo_order():
    """FIFO：先入队的先被 speak 处理。"""
    recorder = _MockSpeakRecorder(delay_s=0.01)
    queue = UtteranceQueue(
        speak=recorder,
        max_queue=5,
        render_timeout_ms=2000,
    )

    await queue.start()
    try:
        await queue.enqueue("utt_a", "第一句")
        await queue.enqueue("utt_b", "第二句")
        await queue.enqueue("utt_c", "第三句")

        # 等所有 speak 完成
        await _wait_for(lambda: len(recorder.calls) == 3, timeout=2.0)

        assert len(recorder.calls) == 3
        # 顺序：与入队顺序一致
        assert [uid for _text, uid in recorder.calls] == [
            "utt_a",
            "utt_b",
            "utt_c",
        ]
        # payload：text 与 utterance_id 都正确传递
        assert recorder.calls[0][0] == "第一句"
        assert recorder.calls[0][1] == "utt_a"
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_queue_serializes_concurrent_invocations():
    """串行：即使多个 utterance 在队列中，speak 一次最多执行一个。"""
    recorder = _MockSpeakRecorder(delay_s=0.05)
    queue = UtteranceQueue(
        speak=recorder,
        max_queue=5,
        render_timeout_ms=2000,
    )

    await queue.start()
    try:
        for i in range(4):
            await queue.enqueue(f"utt_{i}", f"text_{i}")

        # 等待全部完成
        await _wait_for(lambda: len(recorder.calls) == 4, timeout=3.0)

        # 任意时刻 inflight ≤ 1（串行）
        assert recorder._max_inflight == 1, f"队列应串行执行，但观察到最大并发 = {recorder._max_inflight}"
    finally:
        await queue.stop()


# ---------------------------------------------------------------------------
# 容量溢出：丢最旧
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overflow_drops_oldest_with_warning(loguru_capture):
    """队列满时丢最旧，记录 WARNING 日志，新条目仍能入队。"""
    recorder = _MockSpeakRecorder(delay_s=0.2)
    queue = UtteranceQueue(
        speak=recorder,
        max_queue=3,
        render_timeout_ms=2000,
    )

    await queue.start()
    try:
        await queue.enqueue("utt_oldest", "oldest")
        await queue.enqueue("utt_mid", "mid")
        await queue.enqueue("utt_newest_pre", "newest_pre")
        # 这一条会触发丢最旧
        await queue.enqueue("utt_overflow", "overflow")

        # 队首 utt_oldest 应被丢弃（loguru 捕获）
        drop_records = [
            r
            for r in loguru_capture.records
            if r["level"] == "WARNING" and "丢弃最旧 utterance" in r["message"] and "utt_oldest" in r["message"]
        ]
        assert drop_records, (
            f"应记录 utt_oldest 的丢弃 WARN 日志，实际: {[r['message'] for r in loguru_capture.records]}"
        )

        stats = queue.get_stats()
        assert stats["dropped"] >= 1
        assert stats["enqueued"] == 4

        # 等 worker 处理完所有非丢弃条目（3 条）
        await _wait_for(lambda: len(recorder.calls) == 3, timeout=3.0)
        # 确认 utt_oldest 没有被 speak（被丢弃了）
        called_ids = [uid for _text, uid in recorder.calls]
        assert "utt_oldest" not in called_ids
        assert "utt_overflow" in called_ids  # 新条目正常入队
    finally:
        await queue.stop()


# ---------------------------------------------------------------------------
# 单 utterance 超时
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_item_timeout_skips_and_continues(loguru_capture):
    """单 speak 超过 render_timeout_ms → 取消并继续下一条。"""
    invocation_count = 0
    invoke_started = asyncio.Event()

    async def _slow_first_then_fast(text: str, utterance_id: Optional[str]) -> None:
        nonlocal invocation_count
        invocation_count += 1
        if invocation_count == 1:
            invoke_started.set()
            # 比 timeout 长得多
            await asyncio.sleep(2.0)
            return
        # 后续条目立即返回
        return

    queue = UtteranceQueue(
        speak=_slow_first_then_fast,
        max_queue=5,
        render_timeout_ms=300,  # 300ms
    )
    await queue.start()
    try:
        await queue.enqueue("utt_slow", "slow")
        await queue.enqueue("utt_fast", "fast")

        # 等到第一条 speak 启动
        await _wait_for(lambda: invoke_started.is_set(), timeout=1.0)
        # 等 worker 跳过第一条并完成第二条（> 300ms < 1s）
        await _wait_for(lambda: invocation_count >= 2, timeout=2.0)

        assert invocation_count == 2, f"应继续处理下一条，实际只调了 {invocation_count}"
        stats = queue.get_stats()
        assert stats["timeouts"] == 1
        timeout_records = [
            r
            for r in loguru_capture.records
            if r["level"] == "ERROR" and "等待超时" in r["message"] and "utt_slow" in r["message"]
        ]
        assert timeout_records, "应记录超时 ERROR 日志"
    finally:
        await queue.stop()


# ---------------------------------------------------------------------------
# wait_idle / busy（发言等播放的背压原语）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_idle_returns_immediately_when_idle():
    """队列空且 worker 闲：wait_idle 立即返回 True（未启动与已启动都成立）。"""
    queue = UtteranceQueue(speak=_MockSpeakRecorder(), max_queue=5, render_timeout_ms=2000)
    assert await queue.wait_idle(timeout_ms=100) is True

    await queue.start()
    try:
        assert await queue.wait_idle(timeout_ms=100) is True
        assert queue.busy is False
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_wait_idle_blocks_until_processing_completes():
    """正在播放时 busy=True，wait_idle 阻塞到播完才返回 True。"""
    recorder = _MockSpeakRecorder(delay_s=0.3)
    queue = UtteranceQueue(speak=recorder, max_queue=5, render_timeout_ms=2000)
    await queue.start()
    try:
        await queue.enqueue("utt_a", "text")
        await _wait_for(lambda: queue.busy, timeout=1.0)
        assert await queue.wait_idle(timeout_ms=2000) is True
        assert queue.busy is False
        assert len(recorder.calls) == 1
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_wait_idle_times_out_when_busy():
    """播放中 wait_idle 超时返回 False，不抛异常。"""
    speak_started = asyncio.Event()

    async def _long_speak(text: str, utterance_id: Optional[str]) -> None:
        speak_started.set()
        await asyncio.sleep(1.0)

    queue = UtteranceQueue(speak=_long_speak, max_queue=5, render_timeout_ms=5000)
    await queue.start()
    try:
        await queue.enqueue("utt_a", "text")
        await _wait_for(lambda: speak_started.is_set(), timeout=1.0)
        assert await queue.wait_idle(timeout_ms=100) is False
        assert queue.busy is True
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_wait_idle_waits_for_whole_queue_drain():
    """多条积压时 wait_idle 只在全部播完后返回（逐条完成唤醒重判谓词）。"""
    recorder = _MockSpeakRecorder(delay_s=0.1)
    queue = UtteranceQueue(speak=recorder, max_queue=5, render_timeout_ms=2000)
    await queue.start()
    try:
        await queue.enqueue("utt_a", "a")
        await queue.enqueue("utt_b", "b")
        assert await queue.wait_idle(timeout_ms=2000) is True
        assert len(recorder.calls) == 2
        assert queue.busy is False
    finally:
        await queue.stop()


# ---------------------------------------------------------------------------
# 干净停止 / 禁用 no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_cancels_worker_cleanly():
    """stop() 后 worker 退出，未播放条目被丢弃（不进入 TTS）。"""
    recorder = _MockSpeakRecorder(delay_s=0.5)
    queue = UtteranceQueue(
        speak=recorder,
        max_queue=5,
        render_timeout_ms=2000,
    )

    await queue.start()
    await queue.enqueue("utt_before_stop", "text")
    # 不等消费立即停
    await queue.stop()

    assert queue.is_running is False
    # 没有遗留任务
    assert queue._worker_task is None or queue._worker_task.done()


@pytest.mark.asyncio
async def test_enqueue_before_start_buffers_for_later_start():
    """未 start 时 enqueue 不报错（缓冲在内部 deque）；start 后开始消费。"""
    recorder = _MockSpeakRecorder(delay_s=0.0)
    queue = UtteranceQueue(
        speak=recorder,
        max_queue=5,
        render_timeout_ms=2000,
    )

    # 在 start 之前先入队
    accepted = await queue.enqueue("utt_pre_start", "text_pre")
    assert accepted is True
    assert queue.size == 1

    await queue.start()
    try:
        await _wait_for(lambda: len(recorder.calls) == 1, timeout=2.0)
        assert recorder.calls[0][1] == "utt_pre_start"
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_enqueue_after_stop_rejected():
    """stop() 之后的 enqueue 被拒绝并返回 False。"""
    recorder = _MockSpeakRecorder()
    queue = UtteranceQueue(
        speak=recorder,
        max_queue=5,
        render_timeout_ms=2000,
    )
    await queue.start()
    await queue.stop()
    accepted = await queue.enqueue("utt_after_stop", "text")
    assert accepted is False


@pytest.mark.asyncio
async def test_start_is_idempotent():
    """start() 幂等：重复调用不会重建 worker。"""
    recorder = _MockSpeakRecorder()
    queue = UtteranceQueue(
        speak=recorder,
        max_queue=5,
        render_timeout_ms=2000,
    )

    await queue.start()
    task1 = queue._worker_task
    await queue.start()  # 第二次：不应重建
    task2 = queue._worker_task
    assert task1 is task2

    await queue.stop()


# ---------------------------------------------------------------------------
# 构造参数校验
# ---------------------------------------------------------------------------


def test_constructor_rejects_non_callable_speak():
    """speak 必须可调用，否则 TypeError。"""
    with pytest.raises(TypeError, match="speak"):
        UtteranceQueue(speak="not callable")  # type: ignore[arg-type]


def test_constructor_rejects_invalid_max_queue():
    with pytest.raises(ValueError, match="max_queue"):
        UtteranceQueue(speak=_MockSpeakRecorder(), max_queue=0)


def test_constructor_rejects_invalid_render_timeout():
    with pytest.raises(ValueError, match="render_timeout_ms"):
        UtteranceQueue(speak=_MockSpeakRecorder(), render_timeout_ms=10)


def test_defaults_match_design_constants():
    """默认 max_queue=3 / render_timeout_ms=60000（与核心配置一致）。"""
    assert DEFAULT_MAX_QUEUE == 3
    assert DEFAULT_RENDER_TIMEOUT_MS == 60_000


# ---------------------------------------------------------------------------
# 工具：等待异步条件
# ---------------------------------------------------------------------------


async def _wait_for(predicate, *, timeout: float = 2.0, interval: float = 0.01) -> None:
    """忙等 predicate 变 True，超时抛 AssertionError。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"等待条件超时（{timeout}s）")


# 类型标注静态校验（确保 SpeakCallable 与 UtteranceQueue 签名一致）
def _typecheck() -> None:
    speak: SpeakCallable = AsyncMock()
    q: UtteranceQueue = UtteranceQueue(speak=speak)
    _ = q
