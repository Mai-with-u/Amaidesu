"""FileWatcher：启停生命周期、变更回调与路径过滤、防抖、回调超时与失败冷却、统计快照

每个测试在 tmp_path 里自包含；等待留足余量（Windows fsnotify 有延迟），超时/冷却用极短时长加速。
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from typing import Sequence

import pytest

from src.modules.config.file_watcher import FileChange, FileWatcher, FileWatcherStats


@pytest.fixture
def toml_file(tmp_path: Path) -> Path:
    path = tmp_path / "test.toml"
    path.write_text("# test\n", encoding="utf-8")
    return path


@pytest.fixture
async def make_watcher():
    """make_watcher(paths, **kwargs, start=True)：构造并（默认）启动，测试结束统一 stop。"""
    watchers: list[FileWatcher] = []

    async def _make(paths: list[Path], *, start: bool = True, **kwargs) -> FileWatcher:
        watcher = FileWatcher(paths=paths, **kwargs)
        watchers.append(watcher)
        if start:
            await watcher.start()
        return watcher

    yield _make
    for watcher in watchers:
        await watcher.stop()


def _modify(path: Path, content: str = "# updated\n") -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())


async def _write(path: Path, content: str = "# updated\n") -> None:
    await asyncio.to_thread(_modify, path, content)


async def _wait_count(recorder: list, expected: int, timeout_s: float = 3.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while len(recorder) < expected and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)
    return len(recorder) >= expected


async def _quiesce(recorder: list, quiet_s: float, timeout_s: float = 3.0) -> None:
    """连续 quiet_s 无新批次视为在途尾批已清（静默窗口取大于 watchfiles step 50ms 的值）。"""
    loop = asyncio.get_running_loop()
    deadline, last_count, last_change = loop.time() + timeout_s, len(recorder), loop.time()
    while loop.time() < deadline:
        await asyncio.sleep(0.05)
        if len(recorder) != last_count:
            last_count, last_change = len(recorder), loop.time()
        elif loop.time() - last_change >= quiet_s:
            return


def _collector(received: list):
    return lambda changes: received.append(list(changes))


async def test_lifecycle_start_stop_and_idempotence(make_watcher, toml_file) -> None:
    unsubscribed = await make_watcher([Path("ignored.toml")], start=False)
    with pytest.raises(RuntimeError, match="至少注册一个订阅"):
        await unsubscribed.start()
    await unsubscribed.stop()  # 从未 start 的 stop 安全
    assert unsubscribed.running is False

    watcher = await make_watcher([toml_file], start=False)
    watcher.subscribe(lambda changes: None, paths=[toml_file])
    await watcher.start()
    task = watcher._task
    await watcher.start()  # 重复 start 不重建后台任务
    assert watcher.running is True and watcher._task is task
    await watcher.stop()
    await watcher.stop()
    assert watcher.running is False

    with pytest.raises(TypeError, match="可调用"):
        watcher.subscribe("not a function")  # type: ignore[arg-type]


async def test_modify_fires_callbacks_with_resolved_path_until_unsubscribed(make_watcher, toml_file) -> None:
    received_a: list[Sequence[FileChange]] = []
    received_b: list[Sequence[FileChange]] = []
    watcher = await make_watcher([toml_file], start=False, debounce_ms=100)
    sub_a = watcher.subscribe(_collector(received_a), paths=[toml_file])
    watcher.subscribe(_collector(received_b), paths=[toml_file])
    await watcher.start()

    await _write(toml_file)
    assert await _wait_count(received_a, 1) and await _wait_count(received_b, 1)
    assert any(c.path == toml_file.resolve() for batch in received_a for c in batch)

    assert watcher.unsubscribe(sub_a) is True and watcher.unsubscribe(sub_a) is False
    before = len(received_a)
    await _write(toml_file, "# second\n")
    assert await _wait_count(received_b, 2)
    assert len(received_a) == before


async def test_subscription_filters_by_paths_and_nonexistent_file_is_safe(make_watcher, tmp_path) -> None:
    file_a, file_b = tmp_path / "a.toml", tmp_path / "b.toml"
    file_a.write_text("# a\n", encoding="utf-8")
    file_b.write_text("# b\n", encoding="utf-8")
    received: list[Sequence[FileChange]] = []
    watcher = await make_watcher([file_a, file_b], debounce_ms=100, start=False)
    watcher.subscribe(_collector(received), paths=[file_a])
    await watcher.start()

    await _write(file_b)
    await asyncio.sleep(1.0)
    assert received == []
    await _write(file_a)
    assert await _wait_count(received, 1)

    ghost = tmp_path / "never_created.toml"
    safe = await make_watcher([ghost], debounce_ms=100, start=False)
    safe.subscribe(lambda changes: None, paths=[ghost])
    await safe.start()  # 监控不存在的文件不崩溃
    assert safe.running is True


async def test_async_and_sync_callbacks_both_supported(make_watcher, toml_file) -> None:
    received: list[Sequence[FileChange]] = []

    async def async_callback(changes) -> None:
        await asyncio.sleep(0.05)
        received.append(list(changes))

    watcher = await make_watcher([toml_file], start=False, debounce_ms=100)
    watcher.subscribe(async_callback, paths=[toml_file])
    watcher.subscribe(_collector(received), paths=[toml_file])  # sync 回调经 to_thread，不阻塞事件循环
    await watcher.start()
    await _write(toml_file)
    assert await _wait_count(received, 2)


@pytest.mark.parametrize("debounce_ms", [600, 50])
async def test_debounce_never_multiplies_or_drops_events(make_watcher, toml_file, debounce_ms: int) -> None:
    """每笔写入以"回调已收到"为确定性信号再写下一笔（固定 sleep 下事件迟到会被 watchfiles 合并成一条，
    造成假性丢事件）；防抖下回调批次不超过写入数，变更总数不少于写入数。"""
    received: list[Sequence[FileChange]] = []
    watcher = await make_watcher([toml_file], start=False, debounce_ms=debounce_ms)
    watcher.subscribe(_collector(received), paths=[toml_file])
    await watcher.start()

    for i in range(5):
        await _write(toml_file, f"# rapid_{i}\n")
        assert await _wait_count(received, i + 1, timeout_s=5.0), f"写入 #{i} 的事件未送达"
    await _quiesce(received, quiet_s=0.6 if debounce_ms >= 600 else 0.4)

    assert 1 <= len(received) <= 5
    assert sum(len(batch) for batch in received) >= 5


async def test_callback_timeout_does_not_break_watcher(make_watcher, toml_file) -> None:
    recovered: list[Sequence[FileChange]] = []

    def stuck(changes) -> None:
        threading.Event().wait(2.0)  # 被 wait_for 在 0.5s 截断

    watcher = await make_watcher(
        [toml_file], start=False, debounce_ms=100, callback_timeout_s=0.5, callback_failure_threshold=10
    )
    sub_id = watcher.subscribe(stuck, paths=[toml_file])
    await watcher.start()

    await _write(toml_file, "# slow\n")
    await asyncio.sleep(1.5)
    assert watcher.stats.callbacks_timed_out >= 1

    watcher.unsubscribe(sub_id)
    watcher.subscribe(_collector(recovered), paths=[toml_file])
    await _write(toml_file, "# after_timeout\n")
    assert await _wait_count(recovered, 1, timeout_s=3.0), f"超时后 watcher 失能: {watcher.stats}"


async def test_failures_enter_cooldown_that_skips_callbacks(make_watcher, toml_file) -> None:
    calls = 0

    def always_fails(changes) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError(f"intentional failure #{calls}")

    watcher = await make_watcher(
        [toml_file], start=False, debounce_ms=100, callback_timeout_s=1.0,
        callback_failure_threshold=2, callback_cooldown_s=30.0,
    )  # fmt: skip
    sub_id = watcher.subscribe(always_fails, paths=[toml_file])
    await watcher.start()

    for note in ("# fail1\n", "# fail2\n"):
        await _write(toml_file, note)
        await asyncio.sleep(1.5)
    assert watcher.stats.callbacks_failed >= 2
    state = watcher._subscription_states[sub_id]
    assert state.consecutive_failures == 0  # 触发冷却后计数清零
    assert state.cooldown_until_monotonic > asyncio.get_running_loop().time()

    fired = calls
    await _write(toml_file, "# during_cooldown\n")
    await asyncio.sleep(1.0)
    assert calls == fired and watcher.stats.callbacks_skipped_cooldown >= 1


async def test_success_resets_consecutive_failures(make_watcher, toml_file) -> None:
    calls = 0

    def flaky(changes) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("first call fails")

    watcher = await make_watcher(
        [toml_file], start=False, debounce_ms=100, callback_timeout_s=1.0,
        callback_failure_threshold=5, callback_cooldown_s=30.0,
    )  # fmt: skip
    sub_id = watcher.subscribe(flaky, paths=[toml_file])
    await watcher.start()
    state = watcher._subscription_states[sub_id]

    await _write(toml_file, "# fail\n")
    await asyncio.sleep(1.5)
    assert state.consecutive_failures == 1
    await _write(toml_file, "# success\n")
    await asyncio.sleep(1.5)
    assert state.consecutive_failures == 0


async def test_stats_start_at_zero_return_snapshots_and_count_successes(make_watcher, toml_file) -> None:
    received: list[Sequence[FileChange]] = []
    watcher = await make_watcher([toml_file], start=False, debounce_ms=100)
    stats = watcher.stats
    assert isinstance(stats, FileWatcherStats)
    zero = (
        stats.batches_seen, stats.changes_seen, stats.callbacks_succeeded, stats.callbacks_failed,
        stats.callbacks_timed_out, stats.callbacks_skipped_cooldown, stats.restart_count,
    )  # fmt: skip
    assert zero == (0,) * 7
    stats.batches_seen = 999  # type: ignore[misc]
    assert watcher.stats.batches_seen == 0  # 快照而非内部引用

    watcher.subscribe(_collector(received), paths=[toml_file])
    await watcher.start()
    await _write(toml_file)
    assert await _wait_count(received, 1)
    await asyncio.sleep(0.2)  # 让 stats 完成更新
    assert watcher.stats.batches_seen >= 1 and watcher.stats.changes_seen >= 1
    assert watcher.stats.callbacks_succeeded >= 1
