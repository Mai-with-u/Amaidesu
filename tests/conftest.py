"""跨测试域共享的 fixtures；只在某个子域使用的放该子域的 conftest.py。"""

import os
import sqlite3
from pathlib import Path
from typing import Any, AsyncGenerator, Iterator, List, Optional

import pytest
from loguru import logger as _loguru_logger

from src.modules.events.event_bus import EventBus
from src.modules.storage._default_path import DEFAULT_DB_PATH
from src.modules.storage.database import SQLiteDatabase
from src.modules.subtitle.backends.tk_gui_service import SubtitleGuiService


def _sqlite_file(database: Any) -> Optional[Path]:
    """把 ``sqlite3.connect`` 的库参数还原成文件路径；内存库与临时库返回 None。"""
    raw = os.fsdecode(os.fspath(database))
    if raw.startswith("file:"):  # URI 形式，例如只读快照用的 file:<路径>?mode=ro
        raw = raw[len("file:") :].split("?", 1)[0]
    if not raw or raw == ":memory:":
        return None
    return Path(raw).resolve()


@pytest.fixture(autouse=True, scope="session")
def _forbid_project_database() -> Iterator[None]:
    """任何测试打开项目里的 ``data/amaidesu.db`` 就直接失败。

    组合根配置缺 ``[sqlite].db_path`` 时会回退到这个默认库，跑测试就会打开开发者本机的
    真实库并执行迁移。存储层与快照导出都经 ``sqlite3.connect`` 打开库，在这里统一拦下。
    用 ``pytest.fail`` 而不是普通异常：装配代码里的 ``except Exception`` 拦不住它，
    失败一定落到触发的那条用例上。
    """
    project_db = DEFAULT_DB_PATH.resolve()
    real_connect = sqlite3.connect

    def guarded_connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        if _sqlite_file(database) == project_db:
            pytest.fail(f"测试打开了项目真实库 {project_db}：请把 [sqlite].db_path 指向 tmp_path")
        return real_connect(database, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(sqlite3, "connect", guarded_connect)
        yield


@pytest.fixture(autouse=True, scope="session")
def _forbid_real_subtitle_window() -> Iterator[None]:
    """任何测试启动真实的 Tk 字幕窗口就直接失败。

    字幕默认开启 tk_gui 后端：组合根类测试不关字幕，就会在桌面弹出真实窗口，
    窗口线程在用例结束后还会继续写日志，混进后面日志测试的输出文件。
    """

    def refuse(self: SubtitleGuiService) -> None:
        pytest.fail("测试启动了真实的 Tk 字幕窗口：组合根类测试请在配置里设 subtitle.enabled=False")

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(SubtitleGuiService, "start", refuse)
        yield


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
