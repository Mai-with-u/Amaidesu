"""POC: 测试编辑器/工具的不同"保存"模式

目的:验证 MaiBot FileWatcher 在常见编辑器和工具的不同保存方式下的行为。

覆盖场景:
1. **VSCode (auto_save)** — 写到临时文件然后重命名覆盖
2. **VSCode (manual save)** — 截断原文件后写入(保留 inode, Windows 下是覆盖)
3. **Notepad / 一般文本编辑器** — 直接修改内容
4. **tomlkit atomic save** (Amaidesu 自己写回) — 临时文件 + 校验 + 重命名
5. **os.replace (Python 推荐)** — 直接原子替换
6. **多次连续小修改** — 模拟打字场景

用法:
    uv run python scripts/poc_save_patterns.py

输出: 打印到 stdout;调用方可重定向到 .omo/evidence/task-2-save-patterns.txt
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import tomlkit  # noqa: E402

from src.modules.config.file_watcher import FileChange, FileWatcher  # noqa: E402
from src.modules.logging import configure_from_config  # noqa: E402


DEBOUNCE_MS = 600
TEST_TIMEOUT_S = 30.0


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


class EventCollector:
    """收集所有 FileChange 事件并记录延迟"""

    def __init__(self) -> None:
        self.events: list[tuple[float, FileChange]] = []

    def __call__(self, changes: Sequence[FileChange]) -> None:
        for c in changes:
            self.events.append((_now_ms(), c))

    def summary(self) -> dict[str, Any]:
        change_types: dict[str, int] = {}
        paths: set[str] = set()
        for _, c in self.events:
            ct = str(c.change_type)
            change_types[ct] = change_types.get(ct, 0) + 1
            paths.add(str(c.path))
        return {
            "total_events": len(self.events),
            "by_type": change_types,
            "unique_paths": list(paths),
        }


async def _wait_for_events(collector: EventCollector, min_count: int = 1, timeout_s: float = 5.0) -> None:
    """等待直到至少收到 min_count 个事件或超时"""
    waited_ms = 0
    while waited_ms < timeout_s * 1000:
        if len(collector.events) >= min_count:
            return
        await asyncio.sleep(0.05)
        waited_ms += 50


async def _settle() -> None:
    """等待 watchfiles 防抖窗口关闭,确保所有待分发事件落地"""
    await asyncio.sleep((DEBOUNCE_MS + 400) / 1000.0)


def _find_restore_idx(events: list[tuple[float, FileChange]]) -> int:
    """找到"还原写入"事件的索引 (时间轴 50% 之后出现的首个事件认为是 restore)

    简化判断:每次测试中前一半事件算"写入",后一半事件算"还原"。
    """
    if not events:
        return len(events)
    mid_time = events[len(events) // 2][0]
    for i, (t, _) in enumerate(events):
        if t >= mid_time:
            return i
    return len(events)


def _filter_events_before(
    events: list[tuple[float, FileChange]],
    restore_marker: bool,
    restore_event_idx: int,
) -> list[tuple[float, FileChange]]:
    """返回 restore 之前的事件(即本次"写入"产生的所有事件)"""
    if not restore_marker:
        return events
    return events[:restore_event_idx]


# ===================== 保存模式 =====================


async def pattern_vscode_temp_rename(target: Path, collector: EventCollector) -> dict[str, Any]:
    """VSCode 自动保存:写到 .tmp,然后 rename 覆盖"""
    print("\n[Pattern 1] VSCode auto-save: write .tmp -> rename over target")
    initial = len(collector.events)
    before = _now_ms()

    original = target.read_text(encoding="utf-8")
    new_content = original + "\n# vscode_save_marker\n"

    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(new_content)
        f.flush()
        os.fsync(f.fileno())

    # 原子 rename 覆盖
    if target.exists():
        target.unlink()
    tmp.rename(target)

    await _settle()  # 等防抖窗口
    target.write_text(original, encoding="utf-8")  # 还原
    await _settle()

    new_events = collector.events[initial:]
    new_only = _filter_events_before(new_events, restore_marker=True, restore_event_idx=_find_restore_idx(new_events))
    latencies = [t - before for t, _ in new_only]
    return {
        "pattern": "vscode_temp_rename",
        "events_for_write": len(new_only),
        "events_total": len(new_events),
        "summary": collector.summary(),
        "latencies_ms": latencies,
        "max_latency_ms": max(latencies) if latencies else None,
        "pass": len(new_only) > 0,
    }


async def pattern_overwrite_in_place(target: Path, collector: EventCollector) -> dict[str, Any]:
    """手动保存:直接覆盖(截断再写)"""
    print("\n[Pattern 2] Manual save: overwrite in-place (truncate + write)")
    initial = len(collector.events)
    before = _now_ms()

    original = target.read_text(encoding="utf-8")
    new_content = original + "\n# in_place_save_marker\n"

    with target.open("w", encoding="utf-8") as f:
        f.write(new_content)
        f.flush()
        os.fsync(f.fileno())

    await _settle()
    target.write_text(original, encoding="utf-8")  # 还原
    await _settle()

    new_events = collector.events[initial:]
    restore_idx = _find_restore_idx(new_events)
    new_only = _filter_events_before(new_events, restore_marker=True, restore_event_idx=restore_idx)
    latencies = [t - before for t, _ in new_only]
    return {
        "pattern": "overwrite_in_place",
        "events_for_write": len(new_only),
        "events_total": len(new_events),
        "summary": collector.summary(),
        "latencies_ms": latencies,
        "max_latency_ms": max(latencies) if latencies else None,
        "pass": len(new_only) > 0,
    }


async def pattern_tomlkit_atomic(target: Path, collector: EventCollector) -> dict[str, Any]:
    """tomlkit 风格的原子写入 (Amaidesu 自身写回)"""
    print("\n[Pattern 3] tomlkit atomic save (Amaidesu internal)")
    initial = len(collector.events)
    before = _now_ms()

    original = target.read_text(encoding="utf-8")
    doc = tomlkit.loads(original)
    doc["poc_save_marker"] = "atomic"

    tmp = target.with_suffix(".toml.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        tomlkit.dump(doc, f)
        f.flush()
        os.fsync(f.fileno())

    if target.exists():
        target.unlink()
    tmp.rename(target)

    await _settle()
    target.write_text(original, encoding="utf-8")  # 还原
    await _settle()

    new_events = collector.events[initial:]
    restore_idx = _find_restore_idx(new_events)
    new_only = _filter_events_before(new_events, restore_marker=True, restore_event_idx=restore_idx)
    latencies = [t - before for t, _ in new_only]
    return {
        "pattern": "tomlkit_atomic",
        "events_for_write": len(new_only),
        "events_total": len(new_events),
        "summary": collector.summary(),
        "latencies_ms": latencies,
        "max_latency_ms": max(latencies) if latencies else None,
        "pass": len(new_only) > 0,
    }


async def pattern_os_replace(target: Path, collector: EventCollector) -> dict[str, Any]:
    """Python os.replace:原子替换(Windows / POSIX 都支持)"""
    print("\n[Pattern 4] Python os.replace (cross-platform atomic)")
    initial = len(collector.events)
    before = _now_ms()

    original = target.read_text(encoding="utf-8")
    new_content = original + "\n# os_replace_marker\n"

    tmp = target.with_suffix(".toml.osp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(new_content)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp, target)  # 跨平台原子替换

    await _settle()
    target.write_text(original, encoding="utf-8")  # 还原
    await _settle()

    new_events = collector.events[initial:]
    restore_idx = _find_restore_idx(new_events)
    new_only = _filter_events_before(new_events, restore_marker=True, restore_event_idx=restore_idx)
    latencies = [t - before for t, _ in new_only]
    return {
        "pattern": "os_replace",
        "events_for_write": len(new_only),
        "events_total": len(new_events),
        "summary": collector.summary(),
        "latencies_ms": latencies,
        "max_latency_ms": max(latencies) if latencies else None,
        "pass": len(new_only) > 0,
    }


async def pattern_rapid_typing(target: Path, collector: EventCollector) -> dict[str, Any]:
    """模拟连续打字:10 次小写入,触发防抖合并"""
    print("\n[Pattern 5] Rapid typing simulation (10 small writes -> debounce)")
    initial = len(collector.events)
    before = _now_ms()

    original = target.read_text(encoding="utf-8")
    text = original

    # 每次只追加几个字符,模拟编辑器逐字保存
    for i in range(10):
        text = text + f"\n# typing_{i}"
        await asyncio.to_thread(_atomic_write, target, text)

        await asyncio.sleep(0.08)  # 总耗时 800ms,跨越整个防抖窗口

    await asyncio.sleep((DEBOUNCE_MS + 500) / 1000.0)
    target.write_text(original, encoding="utf-8")  # 还原

    new_events = collector.events[initial:]
    batches = _count_batches(new_events, gap_threshold_ms=200.0)
    return {
        "pattern": "rapid_typing",
        "writes": 10,
        "events": len(new_events),
        "estimated_batches": batches,
        "summary": collector.summary(),
        "pass": batches <= 3,  # 防抖应该合并大量事件
        "total_elapsed_ms": _now_ms() - before,
    }


def _atomic_write(target: Path, content: str) -> None:
    """线程内执行的原子写入"""
    with target.open("w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())


def _count_batches(events: list[tuple[float, FileChange]], gap_threshold_ms: float) -> int:
    """粗略估计 batch 数量:相邻事件间隔 > gap_threshold_ms 视为新 batch"""
    if not events:
        return 0
    batches = 1
    for i in range(1, len(events)):
        gap = events[i][0] - events[i - 1][0]
        if gap > gap_threshold_ms:
            batches += 1
    return batches


async def pattern_concurrent_writes(target: Path, collector: EventCollector, n_workers: int = 3) -> dict[str, Any]:
    """并发写入(模拟多个工具同时修改配置)"""
    print(f"\n[Pattern 6] Concurrent writes ({n_workers} workers)")
    initial = len(collector.events)
    before = _now_ms()

    original = target.read_text(encoding="utf-8")

    async def worker(idx: int) -> None:
        def _do() -> None:
            for j in range(3):
                with target.open("a", encoding="utf-8") as f:
                    f.write(f"\n# worker_{idx}_step_{j}")
                    f.flush()
                    os.fsync(f.fileno())
                time.sleep(0.1)

        await asyncio.to_thread(_do)

    await asyncio.gather(*[worker(i) for i in range(n_workers)])
    await asyncio.sleep((DEBOUNCE_MS + 500) / 1000.0)

    target.write_text(original, encoding="utf-8")  # 还原

    new_events = collector.events[initial:]
    batches = _count_batches(new_events, gap_threshold_ms=200.0)
    return {
        "pattern": "concurrent_writes",
        "workers": n_workers,
        "writes_per_worker": 3,
        "events": len(new_events),
        "estimated_batches": batches,
        "summary": collector.summary(),
        "pass": len(new_events) > 0,
        "total_elapsed_ms": _now_ms() - before,
    }


async def pattern_delete_recreate(target: Path, collector: EventCollector) -> dict[str, Any]:
    """删除 + 重新创建文件"""
    print("\n[Pattern 7] Delete + recreate")
    initial = len(collector.events)
    before = _now_ms()

    original = target.read_text(encoding="utf-8")

    target.unlink()
    await asyncio.sleep(0.1)
    target.write_text(original, encoding="utf-8")

    await _wait_for_events(collector, min_count=1, timeout_s=3.0)

    new_events = collector.events[initial:]
    latencies = [t - before for t, _ in new_events]
    return {
        "pattern": "delete_recreate",
        "events": len(new_events),
        "summary": collector.summary(),
        "latencies_ms": latencies,
        "max_latency_ms": max(latencies) if latencies else None,
        "pass": len(new_events) > 0,
    }


# ===================== 主流程 =====================


async def run_all() -> dict[str, Any]:
    """执行所有保存模式测试"""
    # 使用临时目录,不影响真实配置
    tmpdir = Path(tempfile.mkdtemp(prefix="poc_save_patterns_"))
    target = tmpdir / "test_config.toml"
    target.write_text("# test\n[meta]\nversion = '0.1.0'\n", encoding="utf-8")

    configure_from_config(
        {
            "enabled": False,
            "console_level": "WARNING",
        }
    )

    print(f"Target file: {target}")
    print(f"Debounce: {DEBOUNCE_MS}ms")
    print(f"Platform: {sys.platform}")

    collector = EventCollector()
    watcher = FileWatcher(
        paths=[target],
        debounce_ms=DEBOUNCE_MS,
    )
    watcher.subscribe(collector, paths=[target])
    await watcher.start()

    results: list[dict[str, Any]] = []
    overall_pass = True

    patterns: list[Callable[[Path, EventCollector], Awaitable[dict[str, Any]]]] = [
        pattern_vscode_temp_rename,
        pattern_overwrite_in_place,
        pattern_tomlkit_atomic,
        pattern_os_replace,
        pattern_rapid_typing,
        pattern_concurrent_writes,
        pattern_delete_recreate,
    ]

    try:
        for p in patterns:
            r = await p(target, collector)
            results.append(r)
            if not r["pass"]:
                overall_pass = False
            # 给每次测试留点时间让所有事件落地
            await asyncio.sleep(0.2)
    finally:
        await watcher.stop()
        shutil.rmtree(tmpdir, ignore_errors=True)

    return {
        "platform": sys.platform,
        "target_dir": str(tmpdir),
        "debounce_ms": DEBOUNCE_MS,
        "watcher_stats": _stats(watcher),
        "patterns": results,
        "overall_pass": overall_pass,
    }


def _stats(watcher: FileWatcher) -> dict[str, int]:
    s = watcher.stats
    return {
        "batches_seen": s.batches_seen,
        "changes_seen": s.changes_seen,
        "callbacks_succeeded": s.callbacks_succeeded,
        "callbacks_failed": s.callbacks_failed,
        "restart_count": s.restart_count,
    }


def render(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("POC Save-Patterns Test Report")
    lines.append("=" * 78)
    lines.append(f"Platform       : {report['platform']}")
    lines.append(f"Target dir     : {report['target_dir']}")
    lines.append(f"Debounce       : {report['debounce_ms']}ms")
    lines.append("")
    lines.append("FileWatcher stats:")
    for k, v in report["watcher_stats"].items():
        lines.append(f"  {k:32s} = {v}")
    lines.append("")
    lines.append("-" * 78)
    lines.append("Save patterns")
    lines.append("-" * 78)
    for r in report["patterns"]:
        status = "PASS" if r["pass"] else "FAIL"
        lines.append(f"\n[{status}] {r['pattern']}")
        for k, v in r.items():
            if k in {"pattern", "pass", "summary"}:
                continue
            lines.append(f"   {k:24s}: {v}")
        if "summary" in r:
            lines.append("   summary:")
            for sk, sv in r["summary"].items():
                lines.append(f"      - {sk}: {sv}")
    lines.append("")
    lines.append("=" * 78)
    overall = "OVERALL: PASS" if report["overall_pass"] else "OVERALL: FAIL"
    lines.append(overall)
    lines.append("=" * 78)
    return "\n".join(lines)


def main() -> int:
    report = asyncio.run(run_all())
    print(render(report))
    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
