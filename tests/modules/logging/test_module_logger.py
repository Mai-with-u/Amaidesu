"""ModuleLogger 门面单元测试。

验证项目日志门面的级别、模块绑定、exc 参数语义与 raw() 逃生舱。
"""

import pytest
from loguru import logger

from src.modules.logging import ModuleLogger, get_logger


@pytest.fixture
def records():
    """捕获经过 loguru 的原始记录，供断言级别与异常附着。"""
    captured: list = []
    handler_id = logger.add(lambda msg: captured.append(msg.record), level="DEBUG")
    yield captured
    logger.remove(handler_id)


class TestModuleLogger:
    def test_levels_and_module_binding(self, records):
        """四个级别方法逐级透传，module 绑定全程保留。"""
        log = ModuleLogger("facade_test")
        log.debug("d")
        log.info("i")
        log.warning("w")
        log.error("e")

        assert [r["level"].name for r in records] == ["DEBUG", "INFO", "WARNING", "ERROR"]
        assert all(r["extra"]["module"] == "facade_test" for r in records)
        assert all(r["exception"] is None for r in records)

    def test_exception_logs_error_with_stack(self, records):
        """exception() 为 ERROR 级且附着当前异常。"""
        log = ModuleLogger("facade_exc")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            log.exception("处理失败")

        rec = records[-1]
        assert rec["level"].name == "ERROR"
        assert rec["exception"] is not None
        assert rec["exception"].value.args[0] == "boom"

    def test_error_exc_true_inside_except(self, records):
        """error(exc=True) 在 except 块内附着当前异常。"""
        log = ModuleLogger("facade_err")
        try:
            raise ValueError("inner")
        except ValueError:
            log.error("失败", exc=True)

        rec = records[-1]
        assert rec["exception"] is not None
        assert isinstance(rec["exception"].value, ValueError)

    def test_error_with_exception_instance(self, records):
        """exc 可直接传异常对象（如 gather 捞回的结果）。"""
        log = ModuleLogger("facade_inst")
        err = ValueError("pre-built")
        log.error("后端失败", exc=err)

        rec = records[-1]
        assert rec["exception"] is not None
        assert rec["exception"].value is err

    def test_warning_exc_keeps_level(self, records):
        """warning(exc=True) 保持 WARNING 级。"""
        log = ModuleLogger("facade_warn")
        try:
            raise KeyError("k")
        except KeyError:
            log.warning("warn with stack", exc=True)

        rec = records[-1]
        assert rec["level"].name == "WARNING"
        assert rec["exception"] is not None

    def test_exc_outside_except_is_noop(self, records):
        """无活动异常时 exc=True 不报错；记录至多留 (None,..) 占位，无真实堆栈。"""
        log = ModuleLogger("facade_noop")
        log.error("plain", exc=True)

        exc = records[-1]["exception"]
        assert exc is None or exc.type is None

    def test_raw_returns_underlying_loguru_logger(self, records):
        """raw() 返回底层 logger：原生 API 可用，模块绑定保留。"""
        log = get_logger("raw_test")
        raw = log.raw()
        assert callable(raw.bind), "应暴露 loguru 原生 API"

        raw.info("via raw")
        assert records[-1]["extra"]["module"] == "raw_test"

    def test_get_logger_returns_facade(self):
        """get_logger 应返回门面实例。"""
        assert isinstance(get_logger("any"), ModuleLogger)
