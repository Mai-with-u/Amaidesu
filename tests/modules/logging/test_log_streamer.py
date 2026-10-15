"""LogStreamer：异常堆栈字段、落盘目录透传、持久化单例防线与全局重配置后的 sink 重建。"""

import asyncio
from pathlib import Path

import pytest
from loguru import logger

from src.modules.logging import LogStreamer
from src.modules.logging import logger as logger_module
from src.modules.logging.logger import configure_from_config


@pytest.fixture(autouse=True)
def _reset_global_loguru_handlers():
    """configure_from_config 会重挂全局 loguru handlers，测试后摘除本文件新增的 handler 并恢复模块状态，
    避免悬挂的 stderr handler 写向已关闭的 pytest 捕获流，也避免清掉进入测试前的默认 handler。"""
    old_handler_ids = set(logger._core.handlers.keys())
    old_configured = logger_module._CONFIGURED
    old_default_handler_id = logger_module._DEFAULT_HANDLER_ID
    old_tracked = list(logger_module._HANDLER_IDS)

    yield

    for handler_id in list(logger._core.handlers.keys()):
        if handler_id not in old_handler_ids:
            logger.remove(handler_id)
    logger_module._CONFIGURED = old_configured
    logger_module._DEFAULT_HANDLER_ID = old_default_handler_id
    logger_module._HANDLER_IDS.clear()
    logger_module._HANDLER_IDS.extend(old_tracked)


async def _flush() -> None:
    await asyncio.sleep(0.05)  # sink 内 create_task 的缓冲写入需要一个调度周期


async def test_sink_entry_carries_the_exception_stack_only_for_exception_logs() -> None:
    assert LogStreamer(min_level="DEBUG").persist_retention_days == 0  # 默认不滚动删除落盘日志
    streamer = LogStreamer(min_level="DEBUG", max_logs=10)
    await streamer.start()
    try:
        src_logger = logger.bind(module="TestSrc")
        src_logger.info("plain entry")
        try:
            raise ValueError("boom-root-cause")
        except ValueError:
            src_logger.opt(exception=True).error("bad entry")
        await _flush()
        entries = await streamer.get_recent_logs()
    finally:
        await streamer.stop()

    plain = next(e for e in entries if e["message"] == "plain entry")
    bad = next(e for e in entries if e["message"] == "bad entry")
    assert "exception" not in plain
    assert "Traceback (most recent call last)" in bad["exception"] and "ValueError: boom-root-cause" in bad["exception"]


async def test_persist_dir_is_used_as_given_and_receives_the_log_file(tmp_path: Path) -> None:
    """绝对 persist_dir 原样作为落盘目录，不拼项目根（测试隔离的基础）。"""
    target = tmp_path / "persist"
    streamer = LogStreamer(min_level="DEBUG", persist=True, persist_dir=str(target))
    await streamer.start()
    try:
        assert streamer._persist_dir == target.resolve() and streamer._persist_dir.exists()
        logger.bind(module="TestSrc").info("persist-me")
        await _flush()
    finally:
        await streamer.stop()

    (written,) = target.glob("*.jsonl")
    assert "persist-me" in written.read_text(encoding="utf-8")


async def test_second_persist_instance_triggers_the_singleton_warning(tmp_path: Path, loguru_capture) -> None:
    first = LogStreamer(persist=True, persist_dir=str(tmp_path / "a"))
    second = LogStreamer(persist=True, persist_dir=str(tmp_path / "b"))
    await first.start()
    await second.start()
    try:
        await _flush()
    finally:
        await first.stop()
        await second.stop()

    assert any(r["level"] == "WARNING" and "persist LogStreamer 实例" in r["message"] for r in loguru_capture.records)


async def test_global_reconfigure_rebuilds_the_orphaned_streamer_sink(tmp_path: Path) -> None:
    streamer = LogStreamer(persist=True, persist_dir=str(tmp_path / "persist"))
    await streamer.start()
    configure_from_config(
        {"enabled": False, "directory": str(tmp_path / "cfglogs")}
    )  # 内部 remove() 会把 streamer 的 sink 一并摘掉
    logger.bind(module="TestSrc").info("after-reconfigure")
    await _flush()
    entries = await streamer.get_recent_logs()
    await streamer.stop()

    assert any(e["message"] == "after-reconfigure" for e in entries)  # 孤儿化后 sink 已重建，继续接收日志
