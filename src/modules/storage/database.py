"""
SQLiteDatabase —— 存储装配入口（组合根）

## 职责
- **连接与生命周期**：持有 ``SQLiteConnectionManager``（按线程连接 +
  WAL + SAVEPOINT），提供 ``initialize()`` / ``close()``
- **schema 迁移自动应用**：``initialize()`` 时自动 ``CREATE TABLE IF NOT EXISTS``、
  按版本执行 ``migrations/`` 注册表回调并写入 ``schema_migrations``
- **健康自检**：``is_healthy()`` / ``table_exists()`` / 表清单 / 版本号
- **按域装配仓储**：``sessions`` / ``chat`` / ``viewers`` / ``sim`` /
  ``events`` / ``llm`` / ``rundowns`` / ``topics`` 八个域仓储（见
  ``repos/``）。业务消费者按需注入仓储，不经过本类做领域读写
- **原始执行面**：``execute`` 系列保留给"模块私有表自管"的消费者
  （如 SimpleMemory 的 ``_memory_*`` 表）与运维脚本；业务表读写一律走仓储

## 重要不变量
- 一个进程一个 ``SQLiteDatabase`` 单例（管理多线程连接；推荐复用
  ``sqlite_database()`` 工厂，也可独立实例化不同 DB 文件）
- 所有方法都是 ``async``；真同步调用放在 ``_run_in_executor`` 内部（防漏原则）
- 领域方法不在本类：宽门面已退役，按表域切分在 ``repos/``
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, List, Optional

from src.modules.logging import get_logger
from src.modules.storage.connection import SQLiteConnectionManager
from src.modules.storage.migrations import SCHEMA_MIGRATIONS
from src.modules.storage.repos import (
    ChatRepo,
    EventRepo,
    LLMRepo,
    RundownRepo,
    SessionRepo,
    SimRepo,
    TopicRepo,
    ViewerRepo,
)
from src.modules.storage.schema import (
    SCHEMA_VERSION,
    build_schema_sql,
    list_expected_tables,
)


logger = get_logger("SQLiteDatabase")


# =============================================================================
# 单实例工厂
# =============================================================================

_default_db: Optional["SQLiteDatabase"] = None


def sqlite_database() -> "SQLiteDatabase":
    """获取/创建默认 SQLiteDatabase（进程内单例）。"""
    global _default_db
    if _default_db is None:
        from src.modules.storage._default_path import DEFAULT_DB_PATH  # 延迟导入避免循环

        _default_db = SQLiteDatabase(db_path=DEFAULT_DB_PATH)
    return _default_db


# =============================================================================
# SQLiteDatabase 实现
# =============================================================================


class SQLiteDatabase:
    """存储装配入口：连接/生命周期/schema + 按域仓储。

    使用方式：
        db = SQLiteDatabase(Path("data/amaidesu.db"))
        await db.initialize()
        # 领域读写经仓储（组合根按消费者需要分发）
        rows = await db.chat.list_recent_live_chat(live_session_id=1)
        # 私有表自管模块的原始执行面（仅此用途）
        rows = await db.execute("SELECT * FROM _memory_facts")
        await db.close()

    多实例场景（不同 DB 文件）可独立创建；共用一个文件的不同实例
    会共享物理连接（SQLite 进程级锁），但每个实例持有独立的
    ``SQLiteConnectionManager``（按线程连接表互不干扰）。
    """

    def __init__(
        self,
        db_path: Path,
        *,
        timeout: float = 30.0,
        auto_apply_schema: bool = True,
    ) -> None:
        self._db_path = Path(db_path)
        self._manager = SQLiteConnectionManager(self._db_path, timeout=timeout)
        self._auto_apply_schema = auto_apply_schema
        self._initialized = False
        # 按域仓储（同一连接面；消费者按需注入其中一个或几个）
        self.sessions = SessionRepo(self._manager)
        self.chat = ChatRepo(self._manager)
        self.viewers = ViewerRepo(self._manager)
        self.sim = SimRepo(self._manager)
        self.events = EventRepo(self._manager)
        self.llm = LLMRepo(self._manager)
        self.rundowns = RundownRepo(self._manager)
        self.topics = TopicRepo(self._manager)

    # -------------------- 属性 --------------------

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def manager(self) -> SQLiteConnectionManager:
        return self._manager

    @property
    def initialized(self) -> bool:
        return self._initialized

    # -------------------- 生命周期 --------------------

    async def initialize(self) -> None:
        """初始化：建库目录、应用 schema、写入版本记录。幂等。"""
        if self._initialized:
            return

        # 确保父目录存在
        parent = self._db_path.parent
        if parent and not parent.exists():
            await self._run_in_executor(parent.mkdir, parents=True, exist_ok=True)

        # 应用 schema（DDL 全 IF NOT EXISTS，幂等）
        if self._auto_apply_schema:
            await self._run_in_executor(self._apply_schema_blocking)

        self._initialized = True
        logger.info(f"SQLiteDatabase 初始化完成: {self._db_path}")

    async def close(self) -> None:
        """关闭全部线程持有的连接。"""
        await self._run_in_executor(self._manager.close_all)
        self._initialized = False
        logger.info(f"SQLiteDatabase 已关闭: {self._db_path}")

    # -------------------- 健康检查 / 自检 --------------------

    async def is_healthy(self) -> bool:
        """运行 ``SELECT 1`` 检查数据库可达。"""
        try:
            row = await self.execute_fetchone("SELECT 1 AS ok")
            return bool(row and row["ok"] == 1)
        except Exception as exc:  # noqa: BLE001 边界处吸收 + 日志
            logger.warning(f"SQLiteDatabase 健康检查失败: {exc}")
            return False

    async def table_exists(self, table_name: str) -> bool:
        """检查指定表是否存在（sqlite_master）。"""
        rows = await self.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return len(rows) > 0

    async def list_tables(self) -> List[str]:
        """返回当前 sqlite_master 中所有用户表的名称。"""
        rows = await self.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [row["name"] for row in rows]

    async def assert_schema_ready(self) -> None:
        """断言业务表 + schema_migrations 全部存在；缺一即抛 RuntimeError。

        用于启动健康检查；不通过即阻止启动。
        """
        actual = set(await self.list_tables())
        expected = set(list_expected_tables())
        missing = expected - actual
        if missing:
            raise RuntimeError(f"SQLiteDatabase schema 不完整，缺失表: {sorted(missing)}。已存在: {sorted(actual)}")
        logger.debug(f"SQLiteDatabase schema 自检通过: {len(expected)} 张表齐备")

    async def get_schema_version(self) -> int:
        """返回当前已应用的 schema 版本（无记录则 0）。"""
        rows = await self.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1")
        if not rows:
            return 0
        return int(rows[0]["version"])

    # -------------------- 原始执行面（async 包装） --------------------
    # 仅供"模块私有表自管"消费者（SimpleMemory 的 _memory_* 表）与运维/自检
    # 使用；业务表读写一律走 repos/ 的域仓储。

    async def execute(
        self,
        sql: str,
        params: Any = (),
    ) -> List[sqlite3.Row]:
        """执行单条 SQL，返回 ``sqlite3.Row`` 列表。

        - SELECT：返回行列表
        - INSERT/UPDATE/DELETE：返回空行列表，rowcount/ lastrowid 通过
          ``execute_returning`` 拿
        """

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                cursor = conn.execute(sql, params if params is not None else ())
                rows = cursor.fetchall()
                return list(rows)

        return await self._run_in_executor(_exec)

    async def execute_returning(
        self,
        sql: str,
        params: Any = (),
    ) -> sqlite3.Row:
        """执行 INSERT/UPDATE/DELETE 并 RETURNING 一行（如 last_insert_rowid）。"""

        def _exec() -> sqlite3.Row:
            with self._manager.transaction() as conn:
                cursor = conn.execute(sql, params if params is not None else ())
                row = cursor.fetchone()
                return row if row is not None else sqlite3.Row()  # type: ignore[arg-type]

        return await self._run_in_executor(_exec)

    async def execute_fetchone(
        self,
        sql: str,
        params: Any = (),
    ) -> Optional[sqlite3.Row]:
        """fetchone 版便捷调用，未命中返回 ``None``。"""

        def _exec() -> Optional[sqlite3.Row]:
            with self._manager.transaction() as conn:
                cursor = conn.execute(sql, params if params is not None else ())
                return cursor.fetchone()

        return await self._run_in_executor(_exec)

    async def execute_script(self, script_sql: str) -> None:
        """执行多语句脚本（DDL 首选）。自动包裹事务。"""

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.executescript(script_sql)

        await self._run_in_executor(_exec)

    # -------------------- 内部 --------------------

    async def _run_in_executor(self, fn, /, *args, **kwargs):
        """统一 ``asyncio.to_thread`` 防漏（内部所有同步调用都走这里）。"""
        if asyncio.iscoroutinefunction(fn):
            # 不应该到这里（避免失误）；直接 await
            return await fn(*args, **kwargs)
        # functools.partial 处理：kwargs 关键字
        if kwargs:
            return await asyncio.to_thread(partial(fn, *args, **kwargs))
        if args:
            return await asyncio.to_thread(fn, *args)
        return await asyncio.to_thread(fn)

    # -------------------- Schema 应用（同步，仅本类内部） --------------------

    def _apply_schema_blocking(self) -> None:
        """同步执行 schema 应用；由 ``initialize()`` 在 executor 内调度。"""
        # 升版前先快照旧库（备份失败只告警，不阻塞迁移）
        pre_version = self._peek_pre_migration_version_blocking()
        if pre_version is not None:
            self._backup_before_migration(pre_version)

        # 应用 DDL（IF NOT EXISTS 幂等，含最新列）
        with self._manager.transaction() as conn:
            conn.executescript(build_schema_sql())

        # 推进版本：执行 [current+1, SCHEMA_VERSION] 区间内的迁移回调
        # （回调原地修改、幂等），随后写入版本记录
        with self._manager.transaction() as conn:
            existing = conn.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
            current_version = int(existing["version"]) if existing else 0
            if current_version < SCHEMA_VERSION:
                for version in range(current_version + 1, SCHEMA_VERSION + 1):
                    migration = SCHEMA_MIGRATIONS.get(version)
                    if migration is not None:
                        migration(conn)
                    conn.execute(
                        "INSERT OR IGNORE INTO schema_migrations(version, applied_at_ms) VALUES (?, ?)",
                        (version, int(time.time() * 1000)),
                    )
                logger.info(f"SQLiteDatabase schema 已应用: version={SCHEMA_VERSION}（前版本={current_version}）")
            elif current_version > SCHEMA_VERSION:
                logger.warning(
                    f"SQLiteDatabase 数据库 schema 版本 ({current_version}) 高于代码期望 ({SCHEMA_VERSION})。"
                    f"可能是回滚到旧版本；请确认意图。"
                )
            else:
                logger.debug(f"SQLiteDatabase schema 已是当前版本: {SCHEMA_VERSION}")

    def _peek_pre_migration_version_blocking(self) -> Optional[int]:
        """DDL 前探测旧库版本；返回 ``None`` 表示无需备份（全新库或已是最新版本）。"""
        if not self._db_path.exists():
            return None
        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not tables:
                    return None
                if "schema_migrations" not in tables:
                    # 有业务表但无版本记录：前版本时代的旧库，按版本 0 处理
                    return 0
                row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
                version = int(row[0]) if row and row[0] is not None else 0
                return version if version < SCHEMA_VERSION else None
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.warning(f"探测 schema 版本失败（跳过迁移前备份）: {exc}")
            return None

    def _backup_before_migration(self, pre_version: int) -> None:
        """用 SQLite 在线备份 API 快照当前库到 ``<db目录>/backups/``。

        备份文件不自动清理，由用户自行管理；备份失败仅告警，不阻塞迁移
        （启动可用性优先于备份完备性）。
        """
        try:
            backup_dir = self._db_path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            # 微秒精度：避免同秒多次备份（或极快重连）时同名覆盖
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S%f")
            target = backup_dir / f"{self._db_path.stem}-pre-v{SCHEMA_VERSION}-{stamp}.db"
            source = sqlite3.connect(str(self._db_path))
            try:
                destination = sqlite3.connect(str(target))
                try:
                    source.backup(destination)
                finally:
                    destination.close()
            finally:
                source.close()
            logger.info(f"schema 迁移前备份完成（v{pre_version} → v{SCHEMA_VERSION}，不自动清理）: {target}")
        except Exception as exc:  # noqa: BLE001 备份失败不阻塞迁移
            logger.warning(f"schema 迁移前备份失败，继续迁移: {exc}")


__all__ = ["SQLiteDatabase", "sqlite_database"]
