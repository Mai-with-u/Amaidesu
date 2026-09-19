"""LogStreamer sink 的异常堆栈字段测试。"""

import asyncio

from loguru import logger

from src.modules.logging import LogStreamer


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
