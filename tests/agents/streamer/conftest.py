"""tests/agents/streamer 共享 fixture。
"""

from __future__ import annotations

from typing import List, Optional

import pytest
from loguru import logger as _loguru_logger


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