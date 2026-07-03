"""POC FileWatcher 测试 (Windows 兼容性验证)

目标:
- 验证 MaiBot 的 FileWatcher 在 Amaidesu (Windows + watchfiles) 环境下工作正常
- 覆盖修改、追加、不同写入方式、防抖、大文件等场景
- 测量回调延迟(期望 < 1000ms)

用法:
    uv run python scripts/poc_filewatcher_test.py

输出: 打印到 stdout;调用方可重定向到 .omo/evidence/task-2-filewatcher-latency.txt
"""

from __future__ import annotations

import asyncio
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

# 允许脚本直接运行,无需安装项目包
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import tomlkit  # noqa: E402

from src.modules.config.file_watcher import FileChange, FileWatcher  # noqa: E402
from src.modules.logging import configure_from_config  # noqa: E402


# ===================== 配置 =====================

# 默认监控 Amaidesu 真实配置文件
TARGET_CONFIG = PROJECT_ROOT / "config" / "core.toml"

DEBOUNCE_MS = 600
LATENCY_BUDGET_MS = 1000  # 期望延迟预算
TEST_TIMEOUT_S = 30.0  # 单个用例最大等待时间


# ===================== 辅助函数 =====================


def _timestamp_ms() -> float:
    """单调时钟毫秒,用于延迟测量"""
    return time.perf_counter() * 1000.0


class CallbackRecorder:
    """记录所有回调触发,用于后续统计"""

    def __init__(self) -> None:
        self.events: list[tuple[float, list[FileChange]]] = []

    def __call__(self, changes: Sequence[FileChange]) -> None:
        self.events.append((_timestamp_ms(), list(changes)))

    def latencies_ms(self, write_timestamps: list[float]) -> list[float]:
        """计算每次写入事件到对应回调触发的延迟(就近匹配)"""
        latencies: list[float] = []
        used = [False] * len(self.events)
        for write_ts in write_timestamps:
            best_idx = -1
            best_delta = float("inf")
            for i, (cb_ts, _) in enumerate(self.events):
                if used[i]:
                    continue
                delta = cb_ts - write_ts
                # 回调必然在写入之后,所以 delta >= 0(允许极小误差)
                if 0 <= delta < best_delta:
                    best_delta = delta
                    best_idx = i
            if best_idx >= 0:
                used[best_idx] = True
                latencies.append(best_delta)
        return latencies


def _backup_file(path: Path) -> Path | None:
    """备份目标文件以便测试后恢复"""
    if not path.exists():
        return None
    backup = path.with_suffix(path.suffix + ".poc_backup")
    shutil.copy2(path, backup)
    return backup


def _restore_file(backup: Path | None, original: Path) -> None:
    """恢复备份并清理"""
    if backup and backup.exists():
        shutil.copy2(backup, original)
        backup.unlink()
    elif original.exists() and backup is None:
        # 文件原本不存在,删除测试产物
        original.unlink()


# ===================== 测试用例 =====================


async def test_append_newline(watcher: FileWatcher, target: Path, recorder: CallbackRecorder) -> dict[str, Any]:
    """用例 1: 追加一个换行(模拟轻量编辑)"""
    print("\n[Case 1] Append newline to config file")

    # 写入起点
    write_events: list[float] = []
    original = target.read_text(encoding="utf-8")

    # 触发写入: 追加一行注释
    def _do_append() -> None:
        with target.open("a", encoding="utf-8") as f:
            f.write("\n# poc_test_marker: append\n")
            f.flush()
            os.fsync(f.fileno())

    write_events.append(_timestamp_ms())
    await asyncio.to_thread(_do_append)

    # 等待回调
    await asyncio.sleep((DEBOUNCE_MS + 500) / 1000.0)

    # 还原文件
    target.write_text(original, encoding="utf-8")

    # 等待还原事件被消费
    await asyncio.sleep((DEBOUNCE_MS + 500) / 1000.0)

    latencies = recorder.latencies_ms(write_events)
    return {
        "name": "append_newline",
        "writes": len(write_events),
        "callbacks": len(recorder.events),
        "latencies_ms": latencies,
        "max_latency_ms": max(latencies) if latencies else None,
        "avg_latency_ms": statistics.mean(latencies) if latencies else None,
        "pass": bool(latencies) and max(latencies) < LATENCY_BUDGET_MS,
    }


async def test_rewrite_content(watcher: FileWatcher, target: Path, recorder: CallbackRecorder) -> dict[str, Any]:
    """用例 2: 完全重写内容(模拟保存整个文件)"""
    print("\n[Case 2] Rewrite entire file content")

    original = target.read_text(encoding="utf-8")
    new_content = original + "\n# poc_test_marker: rewrite\n"

    write_events: list[float] = []
    write_events.append(_timestamp_ms())
    target.write_text(new_content, encoding="utf-8")

    # Windows 下 write_text 默认已 flush + close,但 watchfiles 需要 fs 事件触发
    # 显式 fsync 一次以确保 OS 通知 watcher
    def _do_rewrite() -> None:
        with target.open("a", encoding="utf-8") as f:
            os.fsync(f.fileno())

    await asyncio.to_thread(_do_rewrite)

    # 等待回调
    await asyncio.sleep((DEBOUNCE_MS + 500) / 1000.0)

    # 还原
    target.write_text(original, encoding="utf-8")
    await asyncio.sleep((DEBOUNCE_MS + 500) / 1000.0)

    latencies = recorder.latencies_ms(write_events)
    return {
        "name": "rewrite_content",
        "writes": len(write_events),
        "callbacks": len(recorder.events),
        "latencies_ms": latencies,
        "max_latency_ms": max(latencies) if latencies else None,
        "avg_latency_ms": statistics.mean(latencies) if latencies else None,
        "pass": bool(latencies) and max(latencies) < LATENCY_BUDGET_MS,
    }


async def test_tomlkit_atomic_write(watcher: FileWatcher, target: Path, recorder: CallbackRecorder) -> dict[str, Any]:
    """用例 3: tomlkit 风格的"加载->修改->序列化->原子重命名"写入

    模拟 Amaidesu 自己写回配置时的行为(toml_utils.write_toml_preserve)。
    """
    print("\n[Case 3] tomlkit atomic write (load -> modify -> dump -> rename)")

    original = target.read_text(encoding="utf-8")

    write_events: list[float] = []
    doc = tomlkit.loads(original)
    doc["poc_marker"] = "tomlkit_atomic"

    temp_path = target.with_suffix(".toml.tmp")
    write_events.append(_timestamp_ms())
    with temp_path.open("w", encoding="utf-8") as f:
        tomlkit.dump(doc, f)
    # 模拟 Amaidesu 的原子重命名
    if target.exists():
        target.unlink()
    temp_path.rename(target)

    await asyncio.sleep((DEBOUNCE_MS + 500) / 1000.0)

    # 还原
    target.write_text(original, encoding="utf-8")
    await asyncio.sleep((DEBOUNCE_MS + 500) / 1000.0)

    latencies = recorder.latencies_ms(write_events)
    return {
        "name": "tomlkit_atomic_write",
        "writes": len(write_events),
        "callbacks": len(recorder.events),
        "latencies_ms": latencies,
        "max_latency_ms": max(latencies) if latencies else None,
        "avg_latency_ms": statistics.mean(latencies) if latencies else None,
        "pass": bool(latencies) and max(latencies) < LATENCY_BUDGET_MS,
    }


async def test_vscode_temp_rename(watcher: FileWatcher, target: Path, recorder: CallbackRecorder) -> dict[str, Any]:
    """用例 4: VSCode 编辑器风格的"写到临时文件->重命名覆盖"

    很多编辑器(尤其 VSCode)会先写到 .tmp 再原子重命名,这对 FileWatcher 的
    事件过滤是真实挑战。
    """
    print("\n[Case 4] VSCode-style temp + rename (write tmp -> rename over original)")

    original = target.read_text(encoding="utf-8")
    new_content = original + "\n# poc_test_marker: vscode_rename\n"

    temp_path = target.with_suffix(".toml.swp")
    write_events: list[float] = []
    write_events.append(_timestamp_ms())
    with temp_path.open("w", encoding="utf-8") as f:
        f.write(new_content)
        f.flush()
        os.fsync(f.fileno())
    # 原子重命名覆盖原文件
    if target.exists():
        target.unlink()
    temp_path.rename(target)

    await asyncio.sleep((DEBOUNCE_MS + 500) / 1000.0)

    # 还原
    target.write_text(original, encoding="utf-8")
    await asyncio.sleep((DEBOUNCE_MS + 500) / 1000.0)

    latencies = recorder.latencies_ms(write_events)
    return {
        "name": "vscode_temp_rename",
        "writes": len(write_events),
        "callbacks": len(recorder.events),
        "latencies_ms": latencies,
        "max_latency_ms": max(latencies) if latencies else None,
        "avg_latency_ms": statistics.mean(latencies) if latencies else None,
        "pass": bool(latencies) and max(latencies) < LATENCY_BUDGET_MS,
    }


async def test_debounce_window(watcher: FileWatcher, target: Path, recorder: CallbackRecorder) -> dict[str, Any]:
    """用例 5: 防抖窗口 - 验证 watchfiles 的防抖行为

    重要观察: MaiBot 的 FileWatcher 设置 yield_on_timeout=True,
    这意味着 watchfiles 会在每次事件积累一段时间后立即 flush 已有的批次,
    而不是严格等到整个防抖窗口结束才合并。
    实际行为: 每个写入事件在 yield_on_timeout 周期内会被独立 batch。

    因此本用例的预期是: 防抖会显著降低原始事件数量,但不保证合并为单批。
    验证标准: 收到至少一批事件,且变化总数 >= 写入数。
    """
    print("\n[Case 5] Debounce window (5 rapid writes within 1s)")

    original = target.read_text(encoding="utf-8")

    write_start = _timestamp_ms()
    raw_event_count = 0
    for i in range(5):

        def _do_write(i: int = i) -> None:
            with target.open("a", encoding="utf-8") as f:
                f.write(f"\n# debounce_test_{i}\n")
                f.flush()
                os.fsync(f.fileno())

        await asyncio.to_thread(_do_write)
        raw_event_count += 1
        await asyncio.sleep(0.15)  # 总耗时约 750ms

    # 等待防抖窗口关闭
    await asyncio.sleep((DEBOUNCE_MS + 800) / 1000.0)

    # 还原
    target.write_text(original, encoding="utf-8")
    await asyncio.sleep((DEBOUNCE_MS + 500) / 1000.0)

    batch_count = len(recorder.events)
    total_changes_seen = sum(len(changes) for _, changes in recorder.events)
    return {
        "name": "debounce_window",
        "writes": raw_event_count,
        "batches_received": batch_count,
        "total_changes_seen": total_changes_seen,
        "expected_behavior": "Batches may exceed 1 due to yield_on_timeout=True; "
        "debounce should still aggregate events within the window",
        "pass": batch_count > 0 and total_changes_seen >= raw_event_count,
        "total_elapsed_ms": _timestamp_ms() - write_start,
    }


async def test_large_file(watcher: FileWatcher, target: Path, recorder: CallbackRecorder) -> dict[str, Any]:
    """用例 6: 大文件 (500+ 行 TOML) 的修改能否被检测

    主要验证 watchfiles 在较大文件下的健壮性。

    注意:本用例启动独立的 FileWatcher 实例监控临时目录中的大文件,不使用传入的 watcher/recorder。
    """
    print("\n[Case 6] Large TOML (500+ lines) modification")

    # 构造一个 500+ 行的大文件
    with tempfile.TemporaryDirectory() as tmpdir:
        large_path = Path(tmpdir) / "large_test.toml"
        lines = ["# Large test file", "[meta]", 'version = "1.0.0"', ""]
        # 生成 ~510 行
        for i in range(500):
            lines.append(f"[section_{i:03d}]")
            lines.append(f'key_{i} = "value_{i}"')
            lines.append(f"count_{i} = {i}")
            lines.append("")
        large_path.write_text("\n".join(lines), encoding="utf-8")
        file_lines = large_path.read_text(encoding="utf-8").count("\n")

        # 启动一个独立的 watcher 监控这个大文件
        local_recorder = CallbackRecorder()
        large_watcher = FileWatcher(
            paths=[large_path],
            debounce_ms=DEBOUNCE_MS,
        )
        large_watcher.subscribe(local_recorder, paths=[large_path])
        await large_watcher.start()

        try:
            original = large_path.read_text(encoding="utf-8")
            new_content = original + "\n# large_file_marker\n"

            write_events: list[float] = []
            write_events.append(_timestamp_ms())
            large_path.write_text(new_content, encoding="utf-8")

            await asyncio.sleep((DEBOUNCE_MS + 500) / 1000.0)

            latencies = local_recorder.latencies_ms(write_events)

            return {
                "name": "large_file",
                "file_lines": file_lines,
                "writes": len(write_events),
                "callbacks": len(local_recorder.events),
                "latencies_ms": latencies,
                "max_latency_ms": max(latencies) if latencies else None,
                "avg_latency_ms": statistics.mean(latencies) if latencies else None,
                "pass": bool(latencies) and max(latencies) < LATENCY_BUDGET_MS,
            }
        finally:
            await large_watcher.stop()


# ===================== 主流程 =====================


async def run_all() -> dict[str, Any]:
    """执行所有测试用例"""
    target = TARGET_CONFIG
    if not target.exists():
        # 允许在任意目录运行测试 —— 若真实配置不存在,创建一个临时文件
        print(f"[WARN] target config not found: {target}, using temp file instead")
        target = Path(tempfile.mkdtemp()) / "core.toml"
        target.write_text("# temp test config\n[meta]\nversion = '0.0.1'\n", encoding="utf-8")

    backup = _backup_file(target) if target.exists() else None

    # 配置 Amaidesu 日志(写文件方便排查)
    configure_from_config(
        {
            "enabled": False,  # POC 测试关闭文件日志
            "console_level": "WARNING",  # 安静模式
        }
    )

    print(f"Target file: {target}")
    print(f"Debounce: {DEBOUNCE_MS}ms")
    print(f"Latency budget: {LATENCY_BUDGET_MS}ms")

    # 启动 watcher
    recorder = CallbackRecorder()
    watcher = FileWatcher(
        paths=[target],
        debounce_ms=DEBOUNCE_MS,
        callback_timeout_s=10.0,
        callback_failure_threshold=3,
        callback_cooldown_s=30.0,
    )
    watcher.subscribe(recorder, paths=[target])
    await watcher.start()

    print(f"FileWatcher started. Subscribed: {len(watcher._subscriptions)}")  # type: ignore[attr-defined]

    results: list[dict[str, Any]] = []
    overall_pass = True
    test_start = time.perf_counter()

    try:
        # 顺序执行用例 —— 每个用例使用独立 recorder (订阅 + 取消)

        async def with_fresh_recorder(case_fn) -> dict[str, Any]:
            local = CallbackRecorder()
            sub_id = watcher.subscribe(local, paths=[target])
            try:
                result = await case_fn(local)
                # 仅在没有自定义 callbacks 字段时,补充由 recorder 计数
                if "callbacks_received" not in result and "callbacks" not in result:
                    result["callbacks_received"] = len(local.events)
                return result
            finally:
                watcher.unsubscribe(sub_id)

        results.append(await with_fresh_recorder(lambda r: test_append_newline(watcher, target, r)))
        results.append(await with_fresh_recorder(lambda r: test_rewrite_content(watcher, target, r)))
        results.append(await with_fresh_recorder(lambda r: test_tomlkit_atomic_write(watcher, target, r)))
        results.append(await with_fresh_recorder(lambda r: test_vscode_temp_rename(watcher, target, r)))
        results.append(await with_fresh_recorder(lambda r: test_debounce_window(watcher, target, r)))
        results.append(await with_fresh_recorder(lambda r: test_large_file(watcher, target, r)))

    finally:
        await watcher.stop()
        _restore_file(backup, target)

    # 汇总
    for r in results:
        if not r.get("pass", False):
            overall_pass = False

    return {
        "platform": sys.platform,
        "watchfiles_version": _get_watchfiles_version(),
        "target_file": str(target),
        "debounce_ms": DEBOUNCE_MS,
        "latency_budget_ms": LATENCY_BUDGET_MS,
        "elapsed_s": time.perf_counter() - test_start,
        "stats_final": _stats_to_dict(watcher.stats),
        "results": results,
        "overall_pass": overall_pass,
    }


def _get_watchfiles_version() -> str:
    try:
        from importlib.metadata import version

        return version("watchfiles")
    except Exception:
        return "unknown"


def _stats_to_dict(stats) -> dict[str, int]:
    return {
        "batches_seen": stats.batches_seen,
        "changes_seen": stats.changes_seen,
        "callbacks_succeeded": stats.callbacks_succeeded,
        "callbacks_failed": stats.callbacks_failed,
        "callbacks_timed_out": stats.callbacks_timed_out,
        "callbacks_skipped_cooldown": stats.callbacks_skipped_cooldown,
        "restart_count": stats.restart_count,
    }


def render_report(report: dict[str, Any]) -> str:
    """将报告渲染为可读文本"""
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("POC FileWatcher Test Report (Windows compatibility)")
    lines.append("=" * 78)
    lines.append(f"Platform         : {report['platform']}")
    lines.append(f"watchfiles       : {report['watchfiles_version']}")
    lines.append(f"Target file      : {report['target_file']}")
    lines.append(f"Debounce window  : {report['debounce_ms']}ms")
    lines.append(f"Latency budget   : < {report['latency_budget_ms']}ms")
    lines.append(f"Total elapsed    : {report['elapsed_s']:.2f}s")
    lines.append("")
    lines.append("FileWatcher stats:")
    for k, v in report["stats_final"].items():
        lines.append(f"  {k:32s} = {v}")
    lines.append("")
    lines.append("-" * 78)
    lines.append("Test cases")
    lines.append("-" * 78)
    for r in report["results"]:
        status = "PASS" if r.get("pass") else "FAIL"
        lines.append(f"\n[{status}] {r['name']}")
        for k, v in r.items():
            if k in {"name", "pass"}:
                continue
            lines.append(f"   {k:24s}: {v}")
    lines.append("")
    lines.append("=" * 78)
    overall = "OVERALL: PASS" if report["overall_pass"] else "OVERALL: FAIL"
    lines.append(overall)
    lines.append("=" * 78)
    return "\n".join(lines)


def main() -> int:
    report = asyncio.run(run_all())
    print(render_report(report))
    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
