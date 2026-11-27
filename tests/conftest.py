"""跨测试域共享的 fixtures；只在某个子域使用的放该子域的 conftest.py。"""

from contextvars import ContextVar
import os
import sqlite3
from pathlib import Path
from typing import Any, AsyncGenerator, Iterator, List, Optional
import uuid

import pytest
from loguru import logger as _loguru_logger

from src.modules.config.multi_file_loader import generate_default_configs
from src.modules.events.event_bus import EventBus
from src.modules.storage._default_path import DEFAULT_DB_PATH
from src.modules.storage.database import SQLiteDatabase
from src.modules.subtitle.backends.tk_gui_service import SubtitleGuiService
from tests.helpers import FakeClock

# 子进程评测自检后缀：本地可用 `-m "not slow"` 跳过，CI 全量仍会执行
_SLOW_SELF_CHECK_SUFFIXES = (
    "_script_self_check_passes",
    "_eval_dry_self_check_passes",
)

# 必须走真实全量迁移/建库路径的存储测试文件特征（不走模板克隆，确保迁移与存储引擎契约真跑）
_REAL_DB_INIT_STEM_KEYWORDS = (
    "migration",
    "schema",
    "sqlite_store",
    "storage_bootstrap",
    "connection",
)

# 按用例控制是否允许 SQLiteDatabase.initialize() 克隆预迁移模板库
_ALLOW_SQLITE_TEMPLATE_CLONE: ContextVar[bool] = ContextVar("allow_sqlite_template_clone", default=True)


def pytest_collection_modifyitems(items: List[pytest.Item]) -> None:
    """为拉起独立 Python 子进程的评测自检用例统一补挂 slow 标记。"""
    slow_marker = pytest.mark.slow
    for item in items:
        if item.name.endswith(_SLOW_SELF_CHECK_SUFFIXES) and not item.get_closest_marker("slow"):
            item.add_marker(slow_marker)


def _needs_real_db_init(node_path: Path) -> bool:
    """判断该测试文件是否属于迁移/表结构/底层存储引擎测试（需要真实跑完整建库迁移）。"""
    posix = node_path.as_posix()
    if "/tests/modules/storage/" not in posix and not posix.startswith("tests/modules/storage/"):
        return False
    stem = node_path.stem.lower()
    return any(keyword in stem for keyword in _REAL_DB_INIT_STEM_KEYWORDS)


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
def _sqlite_template_cloning(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """每 worker 仅做一次完整建库迁移生成干净模板字节，普通测试新建空库时毫秒级克隆落盘。

    触发条件：当前用例未命中迁移/存储引擎专项测试、``auto_apply_schema=True``、
    目标为非项目真实库且文件尚不存在。已有旧库文件、``:memory:``、或迁移/建库专项测试
    一律回退真实 ``SQLiteDatabase.initialize``，保持强隔离且零削弱迁移验证。
    """
    project_db = DEFAULT_DB_PATH.resolve()
    real_initialize = SQLiteDatabase.initialize
    template_dir = tmp_path_factory.mktemp("sqlite_template")
    template_bytes: Optional[bytes] = None

    async def _build_template_bytes() -> bytes:
        # 首次按需构造单文件干净模板：真跑完整 DDL + 全部迁移 -> 关闭连接 -> 刷净 WAL 并切回 DELETE 模式
        tpl_path = template_dir / f"template_{uuid.uuid4().hex}.db"
        tpl_db = SQLiteDatabase(tpl_path)
        await real_initialize(tpl_db)
        await tpl_db.close()
        conn = sqlite3.connect(str(tpl_path))
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA journal_mode=DELETE")
        finally:
            conn.close()
        return tpl_path.read_bytes()

    async def _fast_initialize(self: SQLiteDatabase) -> None:
        nonlocal template_bytes
        if self.initialized:
            return
        db_file = _sqlite_file(self.db_path)
        can_clone = (
            _ALLOW_SQLITE_TEMPLATE_CLONE.get()
            and getattr(self, "_auto_apply_schema", True)
            and db_file is not None
            and db_file != project_db
            and not self.db_path.exists()
        )
        if can_clone:
            if template_bytes is None:
                template_bytes = await _build_template_bytes()
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path.write_bytes(template_bytes)
            self._initialized = True
            return
        await real_initialize(self)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(SQLiteDatabase, "initialize", _fast_initialize)
        yield


@pytest.fixture(scope="session")
def config_template_bytes(tmp_path_factory: pytest.TempPathFactory) -> dict[str, bytes]:
    """每个测试进程生成一次默认配置模板（7 个 TOML 的文件名到字节内容的映射）。

    Schema 生成是确定性的（同进程两次产物逐字节一致，无时间戳与随机内容），
    生成一次后供各域测试字节复制落盘，免去每用例重跑 Schema 生成；
    需要非默认基线内容的测试仍走 generate_default_configs 真生成。
    """
    template_dir = tmp_path_factory.mktemp("config_template")
    generate_default_configs(template_dir)
    return {path.name: path.read_bytes() for path in sorted(template_dir.glob("*.toml"))}


@pytest.fixture(autouse=True)
def _sqlite_template_scope(request: pytest.FixtureRequest) -> Iterator[None]:
    """按当前测试文件切换是否允许 SQLite 模板克隆（迁移/存储引擎测试强制走真实迁移）。"""
    allow = not _needs_real_db_init(Path(request.node.path))
    token = _ALLOW_SQLITE_TEMPLATE_CLONE.set(allow)
    try:
        yield
    finally:
        _ALLOW_SQLITE_TEMPLATE_CLONE.reset(token)


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
def fake_clock() -> FakeClock:
    """可推进的假时钟（``tick(ms)`` 推进时刻）。

    适用：被测代码把时间入口做成可注入的（``now_ms`` 或等形毫秒时刻函数），
    测试注入 ``fake_clock.now_ms`` 后用 tick 显式走时。真实 ``asyncio.sleep`` /
    ``time.monotonic`` 路径不受它控制，等异步条件成立请用条件轮询
    （tests/config/helpers.py 的 ``wait_until``）。
    """
    return FakeClock()


@pytest.fixture
def chars_as_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 token 计数替换为字符数，预算断言与是否装了 tiktoken 词表无关。

    同一 patch 字符串曾复制在多个测试文件；统一从这里取用。
    """
    monkeypatch.setattr("src.modules.context.tokens.count_text_tokens", len)


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
