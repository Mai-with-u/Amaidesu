"""未知名 Collector 硬错语义测试

覆盖：
- instantiate_collector 遇未知名：raise RuntimeError（加载期已硬错拦截，
  此处出现即程序缺陷），消息含名字与合法名单
- SUPPORTED_COLLECTORS 已不包含已退役的注册名（screen）
- 已知名仍正常实例化（冒烟）
"""

from __future__ import annotations

from typing import List, Optional

import pytest
from loguru import logger as _loguru_logger

from src.modules.collectors.factory import SUPPORTED_COLLECTORS, instantiate_collector


class _LoguruCapture:
    """内存里捕获 loguru 日志记录（项目用 loguru，pytest caplog 不适用）。

    每个测试用 fixture 实例化一次；测试结束后清理 sink，避免污染其它用例。
    """

    def __init__(self) -> None:
        self.records: List[dict] = []
        self._sink_id: Optional[int] = None

    def __enter__(self) -> "_LoguruCapture":
        def _sink(message) -> None:
            record = message.record
            self.records.append(
                {
                    "level": record["level"].name,
                    "message": record["message"],
                    "module": record["name"],
                }
            )

        self._sink_id = _loguru_logger.add(_sink, level="DEBUG")
        return self

    def __exit__(self, *exc_info) -> None:
        if self._sink_id is not None:
            _loguru_logger.remove(self._sink_id)
            self._sink_id = None


@pytest.fixture
def loguru_capture():
    """提供 _LoguruCapture 实例，自动管理 sink 生命周期。"""
    cap = _LoguruCapture()
    with cap:
        yield cap


def test_supported_collectors_does_not_include_screen() -> None:
    """退役采集器已从 SUPPORTED_COLLECTORS 中移除"""
    assert "screen" not in SUPPORTED_COLLECTORS
    # 已知有效名仍在册
    assert "console_input" in SUPPORTED_COLLECTORS
    assert "bili_danmaku" in SUPPORTED_COLLECTORS
    assert "bili_danmaku_official" in SUPPORTED_COLLECTORS
    assert "stt" in SUPPORTED_COLLECTORS


def test_instantiate_collector_unknown_raises() -> None:
    """遇未知名：raise RuntimeError，消息含名字与合法名单、缺陷语义提示。"""
    with pytest.raises(RuntimeError) as exc_info:
        instantiate_collector("nonexistent_collector_xyz")

    message = str(exc_info.value)
    assert "nonexistent_collector_xyz" in message
    assert "console_input" in message
    assert "程序缺陷" in message


def test_instantiate_collector_legacy_screen_raises() -> None:
    """退役的 screen 名同样硬错（未知名语义统一，不再优雅跳过）。"""
    with pytest.raises(RuntimeError) as exc_info:
        instantiate_collector("screen")
    assert "screen" in str(exc_info.value)


def test_instantiate_collector_empty_string_raises() -> None:
    """空串视为未知名：raise RuntimeError。"""
    with pytest.raises(RuntimeError):
        instantiate_collector("")


def test_instantiate_collector_known_returns_instance(loguru_capture) -> None:
    """已知名仍正常实例化（冒烟，不触发 warning）"""
    instance = instantiate_collector("console_input")
    assert instance is not None
    assert instance.name == "console_input"
    # 不应有 WARNING 级别日志
    assert not any(record["level"] == "WARNING" for record in loguru_capture.records)
