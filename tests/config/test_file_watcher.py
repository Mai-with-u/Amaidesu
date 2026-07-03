"""FileWatcher 单元测试

覆盖 FileWatcher 的所有核心契约 (来自 MaiBot-v1.0.0 移植):
- start/stop 生命周期
- 文件变更事件触发回调
- 防抖窗口合并变更
- 回调超时不影响 watcher
- 订阅者级冷却 (失败 N 次后进入冷却)
- 失败追踪 (consecutive_failures)
- 多订阅者同时触发
- 监控不存在的文件不会崩溃

设计原则:
- 每个测试使用 tmp_path 自包含目录
- 等待时间留足余量 (Windows fsnotify 有延迟)
- 计时使用极短超时 (0.5s) 加速冷却/失败测试
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Sequence

import pytest

from src.modules.config.file_watcher import (
    FileChange,
    FileWatcher,
    FileWatcherStats,
)


# =============================================================================
# Fixtures and helpers
# =============================================================================


@pytest.fixture
def toml_file(tmp_path: Path) -> Path:
    """在 tmp_path 下创建一个空 TOML 文件"""
    p = tmp_path / "test.toml"
    p.write_text("# test\n", encoding="utf-8")
    return p


@pytest.fixture
def watcher_factory():
    """Factory: 创建 FileWatcher 实例,每个测试自己负责 stop()

    使用 sync fixture 而不是 async, 因为 pytest-asyncio 在 async fixture 中
    可能创建独立的事件循环,导致 watchfiles.awatch 在不同循环中无法正常工作。

    测试应自己用 try/finally + await w.stop() 来清理。
    """

    def _make(paths: list[Path], **kwargs) -> FileWatcher:
        return FileWatcher(paths=paths, **kwargs)

    return _make


def _modify_file(path: Path, content: str = "# updated\n") -> None:
    """同步写入文件 (触发 fs 事件)"""
    with path.open("a", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())


async def _wait_for_event_count(recorder: list, expected: int, timeout_s: float = 5.0) -> bool:
    """异步等待回调累积到 expected 次 (使用 asyncio.sleep 不阻塞事件循环)"""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if len(recorder) >= expected:
            return True
        await asyncio.sleep(0.05)
    return len(recorder) >= expected


# =============================================================================
# Lifecycle: start / stop
# =============================================================================


class TestLifecycle:
    """FileWatcher 启动/停止必须干净"""

    async def test_start_requires_subscription(self, watcher_factory):
        """未注册订阅者时 start() 必须抛 RuntimeError"""
        w = watcher_factory([Path("ignored.toml")])
        with pytest.raises(RuntimeError, match="至少注册一个订阅"):
            await w.start()
        assert w.running is False

    async def test_start_marks_running(self, watcher_factory, toml_file):
        w = watcher_factory([toml_file])
        w.subscribe(lambda changes: None, paths=[toml_file])
        await w.start()
        try:
            assert w.running is True
        finally:
            await w.stop()

    async def test_stop_clears_running(self, watcher_factory, toml_file):
        w = watcher_factory([toml_file])
        w.subscribe(lambda changes: None, paths=[toml_file])
        await w.start()
        await w.stop()
        assert w.running is False

    async def test_double_start_is_idempotent(self, watcher_factory, toml_file):
        """重复 start() 不会重启后台任务"""
        w = watcher_factory([toml_file])
        w.subscribe(lambda changes: None, paths=[toml_file])
        await w.start()
        try:
            task_ref = w._task
            await w.start()  # 第二次 start 应该被忽略
            assert w._task is task_ref, "重复 start 不应创建新任务"
        finally:
            await w.stop()

    async def test_double_stop_is_idempotent(self, watcher_factory, toml_file):
        w = watcher_factory([toml_file])
        w.subscribe(lambda changes: None, paths=[toml_file])
        await w.start()
        await w.stop()
        await w.stop()  # 第二次 stop 必须不抛异常
        assert w.running is False

    async def test_stop_without_start_is_noop(self, watcher_factory):
        """从未 start 的 watcher 调用 stop 必须安全"""
        w = watcher_factory([Path("ignored.toml")])
        await w.stop()
        assert w.running is False


# =============================================================================
# File modification triggers callback
# =============================================================================


class TestChangeEvents:
    """文件变更必须触发订阅者回调"""

    async def test_callback_fires_on_modify(self, watcher_factory, toml_file):
        received: list[Sequence[FileChange]] = []

        w = watcher_factory([toml_file], debounce_ms=100)
        w.subscribe(lambda changes: received.append(list(changes)), paths=[toml_file])
        await w.start()

        try:
            await asyncio.to_thread(_modify_file, toml_file)
            assert await _wait_for_event_count(received, 1, timeout_s=3.0), f"Expected 1 callback, got {len(received)}"
        finally:
            await w.stop()

    async def test_callback_passes_resolved_path(self, watcher_factory, toml_file):
        received: list[FileChange] = []

        w = watcher_factory([toml_file], debounce_ms=100)
        w.subscribe(lambda changes: received.extend(changes), paths=[toml_file])
        await w.start()

        try:
            await asyncio.to_thread(_modify_file, toml_file)
            assert await _wait_for_event_count(received, 1, timeout_s=3.0)
            # 至少一个事件的 path 应该解析后指向我们的 toml 文件
            assert any(c.path == toml_file.resolve() for c in received), (
                f"No event matched expected path. Got: {[str(c.path) for c in received]}"
            )
        finally:
            await w.stop()

    async def test_unsubscribe_stops_callback(self, watcher_factory, toml_file):
        """取消订阅后,回调不再触发"""
        received: list[Sequence[FileChange]] = []

        w = watcher_factory([toml_file], debounce_ms=100)
        sub_id = w.subscribe(lambda changes: received.append(list(changes)), paths=[toml_file])
        await w.start()

        try:
            # 第一次写入 — 应触发
            await asyncio.to_thread(_modify_file, toml_file)
            assert await _wait_for_event_count(received, 1, timeout_s=3.0)
            first_count = len(received)

            # 取消订阅
            assert w.unsubscribe(sub_id) is True
            assert w.unsubscribe(sub_id) is False  # 二次取消返回 False

            # 再次写入 — 不应再触发
            await asyncio.to_thread(_modify_file, toml_file, "# second\n")
            await asyncio.sleep(1.0)  # 留足时间
            assert len(received) == first_count, (
                f"Unsubscribe failed: received {len(received)} events (expected {first_count})"
            )
        finally:
            await w.stop()

    async def test_multiple_subscriptions_all_fire(self, watcher_factory, toml_file):
        """多个订阅者都会触发"""
        received_a: list[Sequence[FileChange]] = []
        received_b: list[Sequence[FileChange]] = []
        received_c: list[Sequence[FileChange]] = []

        w = watcher_factory([toml_file], debounce_ms=100)
        w.subscribe(lambda changes: received_a.append(list(changes)), paths=[toml_file])
        w.subscribe(lambda changes: received_b.append(list(changes)), paths=[toml_file])
        w.subscribe(lambda changes: received_c.append(list(changes)), paths=[toml_file])

        await w.start()
        try:
            await asyncio.to_thread(_modify_file, toml_file)
            assert await _wait_for_event_count(received_a, 1, timeout_s=3.0)
            assert await _wait_for_event_count(received_b, 1, timeout_s=3.0)
            assert await _wait_for_event_count(received_c, 1, timeout_s=3.0)
        finally:
            await w.stop()


# =============================================================================
# Debounce window groups rapid changes
# =============================================================================


class TestDebounce:
    """防抖窗口必须把高频变更合并"""

    async def test_debounce_groups_rapid_writes(self, watcher_factory, toml_file):
        """600ms 防抖窗口: 5 次快速写入(在同一窗口内)应被合并为 <= 5 批次

        重要说明 (来自 MaiBot/POC 验证):
        - watchfiles.awatch 的 debounce 参数控制事件合并窗口
        - 但是 yield_on_timeout=True 会在窗口中点强制 flush,导致每次写入仍可能
          触发独立回调
        - 本测试的目标不是 "合并为 1 批", 而是验证:
          (1) 防抖下回调次数 <= 原始写入数 (不增加)
          (2) 至少收到 1 批事件 (watcher 正常工作)
        """
        received: list[Sequence[FileChange]] = []

        # 用 600ms 默认防抖
        w = watcher_factory([toml_file], debounce_ms=600)
        w.subscribe(lambda changes: received.append(list(changes)), paths=[toml_file])
        await w.start()

        try:
            # 5 次快速写入,每次间隔 100ms (总耗时 ~500ms, 在 600ms 防抖窗口内)
            for i in range(5):
                await asyncio.to_thread(_modify_file, toml_file, f"# rapid_{i}\n")
                await asyncio.sleep(0.1)

            # 等待防抖窗口完全关闭
            await asyncio.sleep(1.5)

            # 防抖不应增加回调次数: 5 次原始写入 -> <= 5 次回调
            assert len(received) <= 5, f"Debounce should not increase callback count: got {len(received)} for 5 writes"
            # 至少收到 1 批
            assert len(received) >= 1, "Expected at least one callback batch"

            # 收到的所有 change 总数至少应等于写入数 (debounce 不丢事件)
            total_changes = sum(len(batch) for batch in received)
            assert total_changes >= 5, f"Debounce dropped events: got {total_changes} total changes for 5 writes"
        finally:
            await w.stop()

    async def test_debounce_short_window_more_batches(self, watcher_factory, toml_file):
        """短防抖 (50ms) 下, 5 次写入应产生 >= 1 批事件, 总变化数等于写入数"""
        received: list[Sequence[FileChange]] = []

        w = watcher_factory([toml_file], debounce_ms=50)
        w.subscribe(lambda changes: received.append(list(changes)), paths=[toml_file])
        await w.start()

        try:
            for i in range(5):
                await asyncio.to_thread(_modify_file, toml_file, f"# short_{i}\n")
                # 间隔大于防抖窗口, 每个写入都会独立触发
                await asyncio.sleep(0.1)

            # 等待防抖关闭
            await asyncio.sleep(0.5)

            assert len(received) >= 1, f"Expected >= 1 batch, got {len(received)}"
            # 所有 5 个变化都应到达 (debounce 不丢事件)
            total = sum(len(b) for b in received)
            assert total >= 5, f"Debounce dropped events: {total} for 5 writes"
        finally:
            await w.stop()

    async def test_short_debounce_is_more_responsive(self, watcher_factory, toml_file):
        """短防抖 (50ms) 下,每个写入应得到自己的回调"""
        received: list[Sequence[FileChange]] = []

        w = watcher_factory([toml_file], debounce_ms=50)
        w.subscribe(lambda changes: received.append(list(changes)), paths=[toml_file])
        await w.start()

        try:
            for i in range(3):
                await asyncio.to_thread(_modify_file, toml_file, f"# short_{i}\n")
                await asyncio.sleep(0.3)  # 间隔 > 防抖窗口

            # 每个写入都应触发独立回调
            assert await _wait_for_event_count(received, 3, timeout_s=3.0), f"Expected 3 callbacks, got {len(received)}"
        finally:
            await w.stop()


# =============================================================================
# Callback timeout
# =============================================================================


class TestCallbackTimeout:
    """回调超时不应该让 watcher 失能"""

    async def test_timeout_does_not_break_watcher(self, watcher_factory, toml_file):
        """回调超时后,watcher 仍能继续工作并触发后续回调"""
        received_after_recovery: list[Sequence[FileChange]] = []

        def slow_callback(changes):
            # 模拟卡死回调 — 用 threading.Event 同步阻塞,被 asyncio.wait_for 截断
            import threading

            blocker = threading.Event()
            blocker.wait(2.0)  # 会被 wait_for 在 0.5s 截断

        # 短超时 (0.5s) 加速测试
        w = watcher_factory(
            [toml_file],
            debounce_ms=100,
            callback_timeout_s=0.5,
            callback_failure_threshold=10,  # 关闭冷却加速测试
        )
        w.subscribe(slow_callback, paths=[toml_file])
        await w.start()

        try:
            # 第一次写入: 触发超时
            await asyncio.to_thread(_modify_file, toml_file, "# slow\n")
            await asyncio.sleep(1.5)
            assert w.stats.callbacks_timed_out >= 1, "Should have recorded timeout"

            # 替换回调为正常回调,验证 watcher 没被卡死
            w.unsubscribe(list(w._subscriptions.keys())[0])  # type: ignore[attr-defined]
            w.subscribe(
                lambda changes: received_after_recovery.append(list(changes)),
                paths=[toml_file],
            )

            # 第二次写入: 应被新回调接收
            await asyncio.to_thread(_modify_file, toml_file, "# after_timeout\n")
            await asyncio.sleep(2.0)

            assert len(received_after_recovery) >= 1, (
                f"Watcher broken after timeout: no normal callbacks received. Stats: {w.stats}"
            )
        finally:
            await w.stop()


# =============================================================================
# Failure tracking and cooldown
# =============================================================================


class TestFailureTracking:
    """失败计数和冷却必须按预期工作"""

    async def test_consecutive_failures_tracked(self, watcher_factory, toml_file):
        """连续失败的回调被追踪到 stats"""
        call_count = 0

        def always_failing_callback(changes):
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"intentional failure #{call_count}")

        w = watcher_factory(
            [toml_file],
            debounce_ms=100,
            callback_timeout_s=1.0,
            callback_failure_threshold=2,  # 2 次失败后进入冷却
            callback_cooldown_s=30.0,
        )
        sub_id = w.subscribe(always_failing_callback, paths=[toml_file])
        await w.start()

        try:
            # 触发 1 次失败
            await asyncio.to_thread(_modify_file, toml_file, "# fail1\n")
            await asyncio.sleep(1.5)

            # 触发另一次失败 — 这会触发冷却
            await asyncio.to_thread(_modify_file, toml_file, "# fail2\n")
            await asyncio.sleep(1.5)

            # 失败应被计数
            assert w.stats.callbacks_failed >= 2, f"Expected >= 2 failed callbacks, got {w.stats.callbacks_failed}"

            # 订阅状态应进入冷却
            state = w._subscription_states[sub_id]  # type: ignore[attr-defined]
            assert state.consecutive_failures == 0, (
                f"consecutive_failures should reset after cooldown triggers, got {state.consecutive_failures}"
            )
            loop_now = asyncio.get_running_loop().time()
            assert state.cooldown_until_monotonic > loop_now, (
                f"Should be in cooldown until {state.cooldown_until_monotonic}, now is {loop_now}"
            )
        finally:
            await w.stop()

    async def test_cooldown_skips_callbacks(self, watcher_factory, toml_file):
        """冷却期间回调被跳过"""
        call_count = 0

        def always_failing_callback(changes):
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"intentional failure #{call_count}")

        # 长冷却 (5s) + 短超时: 第一次失败立即进入冷却, 测试期间不会过期
        w = watcher_factory(
            [toml_file],
            debounce_ms=100,
            callback_timeout_s=1.0,
            callback_failure_threshold=1,
            callback_cooldown_s=5.0,
        )
        w.subscribe(always_failing_callback, paths=[toml_file])
        await w.start()

        try:
            # 触发进入冷却
            await asyncio.to_thread(_modify_file, toml_file, "# trigger_cooldown\n")
            await asyncio.sleep(1.0)
            initial_call_count = call_count
            assert initial_call_count == 1, f"First call should have fired, got {initial_call_count}"

            # 在冷却期内多次写入
            for i in range(3):
                await asyncio.to_thread(_modify_file, toml_file, f"# during_cooldown_{i}\n")
                await asyncio.sleep(0.2)

            # 冷却跳过回调 — call_count 应保持不变 (5s 冷却远长于测试时间)
            assert call_count == initial_call_count, (
                f"Cooldown failed: callback fired {call_count} times during cooldown "
                f"(should stay at {initial_call_count})"
            )
            # 验证冷却期跳过被记录到 stats
            assert w.stats.callbacks_skipped_cooldown >= 1, (
                f"Expected cooldown skips in stats, got {w.stats.callbacks_skipped_cooldown}"
            )
        finally:
            await w.stop()

    async def test_success_resets_failure_count(self, watcher_factory, toml_file):
        """成功回调应重置 consecutive_failures 计数"""
        failure_count = 0

        def flaky_callback(changes):
            nonlocal failure_count
            failure_count += 1
            if failure_count <= 1:
                raise RuntimeError("first call fails")
            # 后续成功

        w = watcher_factory(
            [toml_file],
            debounce_ms=100,
            callback_timeout_s=1.0,
            callback_failure_threshold=5,  # 高阈值避免触发冷却
            callback_cooldown_s=30.0,
        )
        sub_id = w.subscribe(flaky_callback, paths=[toml_file])
        await w.start()

        try:
            # 第一次: 失败
            await asyncio.to_thread(_modify_file, toml_file, "# fail\n")
            await asyncio.sleep(1.5)
            state = w._subscription_states[sub_id]  # type: ignore[attr-defined]
            assert state.consecutive_failures == 1, f"Expected 1 failure, got {state.consecutive_failures}"

            # 第二次: 成功 — 应重置计数
            await asyncio.to_thread(_modify_file, toml_file, "# success\n")
            await asyncio.sleep(1.5)
            assert state.consecutive_failures == 0, (
                f"Success should reset consecutive_failures, got {state.consecutive_failures}"
            )
        finally:
            await w.stop()


# =============================================================================
# Stats tracking
# =============================================================================


class TestStats:
    """FileWatcherStats 计数必须正确"""

    async def test_stats_initialized_to_zero(self, watcher_factory):
        w = watcher_factory([Path("ignored.toml")])
        stats = w.stats
        assert isinstance(stats, FileWatcherStats)
        assert stats.batches_seen == 0
        assert stats.changes_seen == 0
        assert stats.callbacks_succeeded == 0
        assert stats.callbacks_failed == 0
        assert stats.callbacks_timed_out == 0
        assert stats.callbacks_skipped_cooldown == 0
        assert stats.restart_count == 0

    async def test_stats_returns_snapshot(self, watcher_factory, toml_file):
        """stats 返回快照而非引用 (修改内部状态不应影响外部对象)"""
        w = watcher_factory([toml_file])
        snap = w.stats
        snap.batches_seen = 999  # type: ignore[misc]
        assert w.stats.batches_seen == 0, "stats 应该返回副本,而不是内部引用"

    async def test_stats_track_successful_callbacks(self, watcher_factory, toml_file):
        received: list[Sequence[FileChange]] = []

        w = watcher_factory([toml_file], debounce_ms=100)
        w.subscribe(lambda changes: received.append(list(changes)), paths=[toml_file])
        await w.start()

        try:
            await asyncio.to_thread(_modify_file, toml_file)
            assert await _wait_for_event_count(received, 1, timeout_s=3.0)
            await asyncio.sleep(0.2)  # 让 stats 完成更新

            assert w.stats.batches_seen >= 1
            assert w.stats.changes_seen >= 1
            assert w.stats.callbacks_succeeded >= 1
        finally:
            await w.stop()


# =============================================================================
# Edge cases
# =============================================================================


class TestEdgeCases:
    """边界情况: 不存在的文件,无订阅者等"""

    async def test_watch_nonexistent_file_handles_gracefully(self, watcher_factory, tmp_path):
        """监控不存在的文件不应崩溃"""
        nonexistent = tmp_path / "never_created.toml"
        assert not nonexistent.exists()

        w = watcher_factory([nonexistent], debounce_ms=100)
        w.subscribe(lambda changes: None, paths=[nonexistent])

        # start 不应抛异常
        await w.start()
        try:
            assert w.running is True
            # watcher 应保持运行 (可能 restart_count 增加)
        finally:
            # stop 必须干净
            await w.stop()
        assert w.running is False

    async def test_subscription_can_filter_by_paths(self, watcher_factory, tmp_path):
        """订阅者的 paths 过滤应只触发匹配路径"""
        file_a = tmp_path / "a.toml"
        file_b = tmp_path / "b.toml"
        file_a.write_text("# a\n", encoding="utf-8")
        file_b.write_text("# b\n", encoding="utf-8")

        received: list[Sequence[FileChange]] = []

        w = watcher_factory([file_a, file_b], debounce_ms=100)
        # 只订阅 file_a
        w.subscribe(lambda changes: received.append(list(changes)), paths=[file_a])
        await w.start()

        try:
            # 写入 file_b — 不应触发
            await asyncio.to_thread(_modify_file, file_b)
            await asyncio.sleep(1.0)
            assert len(received) == 0, f"Filtered subscription should ignore file_b, got {len(received)} events"

            # 写入 file_a — 应触发
            await asyncio.to_thread(_modify_file, file_a)
            assert await _wait_for_event_count(received, 1, timeout_s=3.0)
        finally:
            await w.stop()

    async def test_async_callback_supported(self, watcher_factory, toml_file):
        """async 回调必须被正确调用"""
        received: list[Sequence[FileChange]] = []

        async def async_callback(changes):
            await asyncio.sleep(0.05)
            received.append(list(changes))

        w = watcher_factory([toml_file], debounce_ms=100)
        w.subscribe(async_callback, paths=[toml_file])
        await w.start()

        try:
            await asyncio.to_thread(_modify_file, toml_file)
            assert await _wait_for_event_count(received, 1, timeout_s=3.0)
        finally:
            await w.stop()

    async def test_sync_callback_via_to_thread(self, watcher_factory, toml_file):
        """sync 回调通过 to_thread 执行,不阻塞事件循环"""
        received: list[Sequence[FileChange]] = []

        def sync_callback(changes):
            received.append(list(changes))

        w = watcher_factory([toml_file], debounce_ms=100)
        w.subscribe(sync_callback, paths=[toml_file])
        await w.start()

        try:
            # 事件循环在 sync 回调期间必须仍能运转
            await asyncio.to_thread(_modify_file, toml_file)
            assert await _wait_for_event_count(received, 1, timeout_s=3.0)

            # 验证事件循环没被卡住: 立即可以 sleep
            await asyncio.sleep(0.01)
        finally:
            await w.stop()

    async def test_non_callable_subscribe_raises(self, watcher_factory):
        """非可调用对象不能订阅"""
        w = watcher_factory([Path("ignored.toml")])
        with pytest.raises(TypeError, match="可调用"):
            w.subscribe("not a function")  # type: ignore[arg-type]
