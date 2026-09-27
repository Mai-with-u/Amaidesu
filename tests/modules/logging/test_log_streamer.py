"""LogStreamer sink 的异常堆栈字段测试。"""

import asyncio
from pathlib import Path

import pytest
from loguru import logger

from src.modules.logging import LogStreamer
from src.modules.logging.logger import configure_from_config


@pytest.fixture(autouse=True)
def _reset_global_loguru_handlers():
    """configure_from_config 会重挂全局 loguru handlers，测试后摘除本文件新增的
    handler 并恢复模块状态，避免悬挂的 stderr handler 写向已关闭的 pytest 捕获流，
    也避免清掉进入测试前的默认 handler 影响后续测试文件。"""
    from src.modules.logging import logger as logger_module

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


def test_sink_entry_contains_exception_field():
    """异常日志经 sink 进缓冲应携带 exception 字段，普通日志不带。"""

    async def scenario():
        streamer = LogStreamer(min_level="DEBUG", max_logs=10)
        await streamer.start()
        try:
            src_logger = logger.bind(module="TestSrc")
            src_logger.info("plain entry")
            try:
                raise ValueError("boom-root-cause")
            except ValueError:
                src_logger.opt(exception=True).error("bad entry")
            # 等待 sink 内 create_task 的缓冲写入完成
            await asyncio.sleep(0.05)
            return await streamer.get_recent_logs()
        finally:
            await streamer.stop()

    entries = asyncio.run(scenario())

    plain = next(e for e in entries if e["message"] == "plain entry")
    bad = next(e for e in entries if e["message"] == "bad entry")
    assert "exception" not in plain, "普通日志不应引入 exception 字段"
    assert "Traceback (most recent call last)" in bad["exception"]
    assert "ValueError: boom-root-cause" in bad["exception"]


def test_persist_dir_is_honored_as_is(tmp_path: Path) -> None:
    """persist_dir 透传：绝对路径原样作为落盘目录，不拼项目根（测试隔离的基础）。"""

    async def scenario():
        target = tmp_path / "persist"
        streamer = LogStreamer(min_level="DEBUG", persist=True, persist_dir=str(target))
        await streamer.start()
        try:
            assert streamer._persist_dir == target.resolve(), "绝对 persist_dir 应原样生效"
            assert streamer._persist_dir.exists(), "落盘目录应已创建"
            logger.bind(module="TestSrc").info("persist-me")
            await asyncio.sleep(0.05)
        finally:
            await streamer.stop()

    asyncio.run(scenario())

    files = list((tmp_path / "persist").glob("*.jsonl"))
    assert len(files) == 1, "落盘应只写进指定临时目录"
    assert "persist-me" in files[0].read_text(encoding="utf-8")


def test_second_persist_instance_warns(tmp_path: Path, loguru_capture) -> None:
    """同进程已有存活 persist 实例时，第二个 persist 实例创建应触发单例防线 warning。"""

    async def scenario():
        first = LogStreamer(persist=True, persist_dir=str(tmp_path / "a"))
        await first.start()
        second = LogStreamer(persist=True, persist_dir=str(tmp_path / "b"))
        await second.start()
        try:
            logger.bind(module="TestSrc").info("during-dup")
            await asyncio.sleep(0.05)
        finally:
            await first.stop()
            await second.stop()

    asyncio.run(scenario())

    warnings = [r for r in loguru_capture.records if r["level"] == "WARNING" and "persist LogStreamer 实例" in r["message"]]
    assert warnings, "第二个 persist 实例创建应触发单例防线 warning"


def test_configure_rebuilds_orphaned_streamer_sink(tmp_path: Path) -> None:
    """configure_from_config 全局摘 sink 后，存活 LogStreamer 应被重建（孤儿化恢复）。"""

    async def scenario():
        streamer = LogStreamer(persist=True, persist_dir=str(tmp_path / "persist"))
        await streamer.start()
        # 全局重配置：内部 remove() 会把 streamer 的 sink 一并摘掉
        configure_from_config({"enabled": False, "directory": str(tmp_path / "cfglogs")})
        logger.bind(module="TestSrc").info("after-reconfigure")
        await asyncio.sleep(0.05)
        entries = await streamer.get_recent_logs()
        await streamer.stop()
        return entries

    entries = asyncio.run(scenario())
    assert any(e["message"] == "after-reconfigure" for e in entries), (
        "configure_from_config 后存活 LogStreamer 的 sink 应已重建，继续接收日志"
    )
