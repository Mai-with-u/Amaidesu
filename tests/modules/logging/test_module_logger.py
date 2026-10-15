"""ModuleLogger 门面：级别透传、模块绑定、exc 参数语义与 raw() 逃生舱。"""

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


def test_levels_pass_through_with_the_module_binding_and_no_exception(records) -> None:
    log = ModuleLogger("facade_test")
    log.debug("d")
    log.info("i")
    log.warning("w")
    log.error("e")

    assert [r["level"].name for r in records] == ["DEBUG", "INFO", "WARNING", "ERROR"]
    assert all(r["extra"]["module"] == "facade_test" and r["exception"] is None for r in records)


def test_exception_logs_error_level_with_the_active_stack(records) -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        ModuleLogger("facade_exc").exception("处理失败")

    assert records[-1]["level"].name == "ERROR" and records[-1]["exception"].value.args[0] == "boom"


def test_exc_true_inside_except_attaches_the_stack_and_keeps_the_level(records) -> None:
    log = ModuleLogger("facade_exc")
    try:
        raise ValueError("inner")
    except ValueError:
        log.error("失败", exc=True)
        log.warning("warn with stack", exc=True)

    error, warning = records
    assert (error["level"].name, warning["level"].name) == ("ERROR", "WARNING")  # 附着堆栈不改级别
    assert isinstance(error["exception"].value, ValueError) and warning["exception"] is not None


def test_exc_accepts_a_prebuilt_exception_instance_and_is_a_noop_without_an_active_one(records) -> None:
    log = ModuleLogger("facade_inst")
    prebuilt = ValueError("pre-built")  # 如 gather 捞回的结果
    log.error("后端失败", exc=prebuilt)
    assert records[-1]["exception"].value is prebuilt

    log.error("plain", exc=True)  # 无活动异常：不报错，记录至多留占位，无真实堆栈
    assert records[-1]["exception"] is None or records[-1]["exception"].type is None


def test_get_logger_returns_the_facade_and_raw_exposes_loguru_with_the_binding(records) -> None:
    log = get_logger("raw_test")
    assert isinstance(log, ModuleLogger) and callable(log.raw().bind)  # 原生 API 可用

    log.raw().info("via raw")
    assert records[-1]["extra"]["module"] == "raw_test"
