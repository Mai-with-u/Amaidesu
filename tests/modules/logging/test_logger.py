"""日志系统：JSONL/文本输出、控制台专用、目录创建与回退、轮转配置、重配置清理、默认处理器、级别过滤、异常堆栈字段"""

import json
import os
import re
from pathlib import Path

import pytest
from loguru import logger

from src.modules.logging import configure_from_config, get_logger
from src.modules.logging import logger as logger_module


@pytest.fixture(autouse=True)
def reset_logger_state():
    """测试间保存并恢复 logger 全局状态，并清掉测试期间添加的 loguru 处理器。"""
    configured = logger_module._CONFIGURED
    handler_ids = logger_module._HANDLER_IDS.copy()
    default_handler_id = logger_module._DEFAULT_HANDLER_ID
    yield
    logger_module._CONFIGURED = configured
    logger_module._HANDLER_IDS.clear()
    logger_module._HANDLER_IDS.extend(handler_ids)
    logger_module._DEFAULT_HANDLER_ID = default_handler_id
    for handler_id in list(logger._core.handlers.keys()):
        logger.remove(handler_id)


@pytest.fixture
def log_dir(tmp_path) -> Path:
    directory = tmp_path / "logs"
    directory.mkdir()
    return directory


def _configure(directory: Path, fmt: str = "jsonl", **extra):
    configure_from_config({"enabled": True, "format": fmt, "directory": str(directory), **extra})


def _jsonl(directory: Path) -> list[dict]:
    (path,) = directory.glob("*.jsonl")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_jsonl_entries_have_structure_module_binding_and_level_filtering(log_dir: Path) -> None:
    _configure(log_dir, level="WARNING")
    a, b = get_logger("module_a"), get_logger("module_b")
    a.debug("Debug message")
    a.info("Info message")
    a.warning("Warning from A")
    b.error("Error from B")

    entries = _jsonl(log_dir)
    assert [
        (e["level"], e["module"], e["message"]) for e in entries
    ] == [  # 只有 WARNING 及以上，且模块名按 logger 绑定
        ("WARNING", "module_a", "Warning from A"),
        ("ERROR", "module_b", "Error from B"),
    ]
    assert {"timestamp", "level", "module", "message"} <= set(entries[0])


def test_text_format_has_module_level_message_and_millisecond_timestamp(log_dir: Path) -> None:
    _configure(log_dir, "text", level="INFO")
    get_logger("text_test").info("Text format message")
    content = max(log_dir.glob("*.log"), key=os.path.getctime).read_text(encoding="utf-8")
    assert "text_test" in content and "Text format message" in content and "INFO" in content
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}", content)


def test_console_only_mode_creates_no_files(log_dir: Path) -> None:
    configure_from_config({"enabled": False, "directory": str(log_dir)})
    get_logger("console_test").info("Console only message")
    assert list(log_dir.glob("*")) == []


def test_missing_directory_is_created_and_uncreatable_one_falls_back_to_console(tmp_path) -> None:
    new_dir = tmp_path / "new_logs_dir"
    _configure(new_dir, "text")
    assert new_dir.exists()

    impossible = "Z:\\nonexistent\\log_dir" if os.name == "nt" else "/nonexistent_root/log_dir"
    configure_from_config({"enabled": True, "format": "text", "directory": impossible})
    get_logger("fallback_test").info("Fallback message")  # 优雅降级为仅控制台，不抛


def test_reconfigure_replaces_handlers_and_console_level_is_independent(log_dir: Path) -> None:
    _configure(
        log_dir, "text", level="DEBUG", console_level="ERROR", rotation="5 MB", retention="3 days", compression="gz"
    )
    assert len(logger_module._HANDLER_IDS) == 2  # stderr + 文件，轮转配置被接受

    log = get_logger("console_level_test")
    for method in (log.debug, log.info, log.warning, log.error):
        method(f"{method.__name__} message")
    content = max(log_dir.glob("*.log"), key=os.path.getctime).read_text(encoding="utf-8")
    assert all(
        f"{level} message" in content for level in ("debug", "info", "warning", "error")
    )  # 文件级别独立于控制台级别

    _configure(log_dir, "jsonl", level="WARNING")  # 重配置前先移除旧处理器
    assert len(logger_module._HANDLER_IDS) == 2 and len(logger._core.handlers) == 2


def test_default_handler_is_lazy_and_replaced_by_explicit_configuration() -> None:
    logger_module._DEFAULT_HANDLER_ID = None
    get_logger("default_test").info("Default handler test")  # 未配置时延迟创建默认处理器
    assert logger_module._DEFAULT_HANDLER_ID is not None

    configure_from_config({"enabled": False})
    assert logger_module._DEFAULT_HANDLER_ID is None  # 显式配置后移除默认处理器

    logger_module._CONFIGURED = False
    configure_from_config(None)  # None 配置走默认值
    assert logger_module._CONFIGURED


@pytest.mark.parametrize(
    ("raise_exc", "call", "level", "traceback_text"),
    [
        (ValueError("boom-root-cause"), lambda log: log.exception("记录异常"), "ERROR", "ValueError: boom-root-cause"),
        (
            KeyError("missing-key"),
            lambda log: log.warning("工具执行异常", exc=True),
            "WARNING",
            "KeyError: 'missing-key'",
        ),
    ],
    ids=["exception", "warning-exc-true"],
)
def test_jsonl_exception_entries_carry_the_full_traceback(
    log_dir: Path, raise_exc, call, level, traceback_text
) -> None:
    _configure(log_dir)
    log = get_logger("exc_test")
    try:
        raise raise_exc
    except Exception:
        call(log)
    (entry,) = _jsonl(log_dir)
    assert entry["level"] == level  # exc=True 不改变日志级别
    assert "Traceback (most recent call last)" in entry["exception"] and traceback_text in entry["exception"]


def test_jsonl_exception_key_only_appears_for_real_exceptions(log_dir: Path) -> None:
    _configure(log_dir)
    log = get_logger("plain_test")
    log.info("plain entry")
    log.error("无活动异常的 exc=True", exc=True)
    plain, no_active_exception = _jsonl(log_dir)
    assert set(plain) == {"timestamp", "level", "module", "message"}  # 普通日志字段集不变
    assert "exception" not in no_active_exception  # 占位异常不写入 JSONL
