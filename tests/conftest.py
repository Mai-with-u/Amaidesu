"""跨测试域共享的 fixtures；只在某个子域使用的放该子域的 conftest.py。"""

from pathlib import Path
from typing import AsyncGenerator, List, Optional

import pytest
from loguru import logger as _loguru_logger

from src.modules.events.event_bus import EventBus
from src.modules.storage.database import SQLiteDatabase


class _LoguruCapture:
    """内存里捕获 loguru 日志记录（项目用 loguru，pytest caplog 不适用）。"""

    def __init__(self) -> None:
        self.records: List[dict] = []
        self._sink_id: Optional[int] = None

    def __enter__(self) -> "_LoguruCapture":
        def _sink(message) -> None:
            record = message.record
            self.records.append({"level": record["level"].name, "message": record["message"], "module": record["name"]})

        self._sink_id = _loguru_logger.add(_sink, level="DEBUG")
        return self

    def __exit__(self, *exc_info) -> None:
        if self._sink_id is not None:
            _loguru_logger.remove(self._sink_id)
            self._sink_id = None


@pytest.fixture
def loguru_capture():
    """已进入捕获的 _LoguruCapture；测试直接读 ``records``。

    断言"某条日志是否/是否只出现一次"的测试用它。再写 ``with loguru_capture``
    会把同一个 sink 挂第二次，每条日志记两遍，按条数断言就会失真。
    """
    with _LoguruCapture() as cap:
        yield cap


@pytest.fixture
async def event_bus() -> AsyncGenerator[EventBus, None]:
    bus = EventBus()
    yield bus
    await bus.cleanup()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
async def store(db_path: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    """已建好全部表的临时 SQLite 库。"""
    db = SQLiteDatabase(db_path)
    await db.initialize()
    yield db
    await db.close()
