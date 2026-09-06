"""
SQLiteStore —— 异步友好的 SQLite 访问层

## 设计目标
- **store 内部统一防漏**：所有同步 sqlite 调用统一在 ``asyncio.to_thread``
  中执行，调用方不需手动 to_thread（避免"忘 to_thread"坑）
- **透明封装**：把 ``SQLiteConnectionManager`` 的同步 API 映射成 async
- **schema 迁移自动应用**：``initialize()`` 时自动 ``CREATE TABLE IF NOT EXISTS``、
  按版本执行 ``SCHEMA_MIGRATIONS`` 迁移回调并写入 ``schema_migrations``
- **诊断输出**：提供 ``is_healthy()`` / ``table_exists()`` / 表清单等自检方法

## 重要不变量
- 一个进程一个 ``SQLiteStore`` 单例（管理多线程连接，无需单实例也行；
  推荐复用 ``sqlite_store()`` 工厂，``SQLiteStore`` 也可独立实例化）
- 所有方法都是 ``async``
- 真同步调用放在 ``_run_in_executor`` 内部（防漏原则）

## 验收
- 测试 ``tests/modules/storage/`` 覆盖：
  - 13 张业务表 + 模块私有表全部创建（schema 统一建表）
  - schema_migrations 记录并单调推进到当前版本（迁移回调幂等）
  - simulated 列存在且默认 False
  - live_sessions 场次开行/结账/级联删除、llm_usage 插入等领域方法
  - ``table_exists`` / ``list_tables`` / ``is_healthy``
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.modules.logging import get_logger
from src.modules.storage.connection import ManagedSQLiteConnection, SQLiteConnectionManager
from src.modules.storage.schema import (
    SCHEMA_MIGRATIONS,
    SCHEMA_VERSION,
    build_schema_sql,
    list_expected_tables,
)
from src.modules.time_utils import now_ms


logger = get_logger("SQLiteStore")


# =============================================================================
# 单实例工厂
# =============================================================================

_default_store: Optional["SQLiteStore"] = None


def sqlite_store() -> "SQLiteStore":
    """获取/创建默认 SQLiteStore（进程内单例）。"""
    global _default_store
    if _default_store is None:
        from src.modules.storage._default_path import DEFAULT_DB_PATH  # 延迟导入避免循环

        _default_store = SQLiteStore(db_path=DEFAULT_DB_PATH)
    return _default_store


def set_default_store(store: Optional["SQLiteStore"]) -> None:
    """设置/清除默认 SQLiteStore（用于测试或自定义路径）。"""
    global _default_store
    _default_store = store


# =============================================================================
# SQLiteStore 实现
# =============================================================================


class SQLiteStore:
    """异步 SQLite 访问层。

    使用方式：
        store = SQLiteStore(Path("data/amaidesu.db"))
        await store.initialize()
        # ... 业务使用
        rows = await store.execute("SELECT * FROM live_chat WHERE simulated=0")
        await store.close()

    多实例场景（不同 DB 文件）可独立创建；共用一个文件的不同 store
    会共享物理连接（SQLite 进程级锁），但每个 store 持有独立的
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
        logger.info(f"SQLiteStore 初始化完成: {self._db_path}")

    async def close(self) -> None:
        """关闭全部线程持有的连接。"""
        await self._run_in_executor(self._manager.close_all)
        self._initialized = False
        logger.info(f"SQLiteStore 已关闭: {self._db_path}")

    # -------------------- 健康检查 / 自检 --------------------

    async def is_healthy(self) -> bool:
        """运行 ``SELECT 1`` 检查数据库可达。"""
        try:
            row = await self.execute_fetchone("SELECT 1 AS ok")
            return bool(row and row["ok"] == 1)
        except Exception as exc:  # noqa: BLE001 边界处吸收 + 日志
            logger.warning(f"SQLiteStore 健康检查失败: {exc}")
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
        """断言 13 张表 + schema_migrations 全部存在；缺一即抛 RuntimeError。

        用于启动健康检查；不通过即阻止启动（与 MaiBot 行为一致）。
        """
        actual = set(await self.list_tables())
        expected = set(list_expected_tables())
        missing = expected - actual
        if missing:
            raise RuntimeError(f"SQLiteStore schema 不完整，缺失表: {sorted(missing)}。已存在: {sorted(actual)}")
        logger.debug(f"SQLiteStore schema 自检通过: {len(expected)} 张表齐备")

    async def get_schema_version(self) -> int:
        """返回当前已应用的 schema 版本（无记录则 0）。"""
        rows = await self.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1")
        if not rows:
            return 0
        return int(rows[0]["version"])

    # -------------------- SQL 执行（async 包装） --------------------

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

    # -------------------- 领域写入方法 --------------------
    # 三表均带 simulated 贯穿列（schema.py:149/163/177），详见 schema.py "命名硬规则"。
    # 这里只承接 RoomMessagePayload → 表的写入入口；表结构权威在 schema.py，本层不复制。

    async def insert_live_chat(
        self,
        *,
        live_session_id: int,
        timestamp_ms: int,
        sender_role: str,
        content: str,
        message_type: str,
        sender_id: Optional[str] = None,
        sender_name: Optional[str] = None,
        message_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
        tool_result: Optional[str] = None,
        simulated: bool = False,
    ) -> int:
        """插入一条 live_chat 行，返回 lastrowid。

        ``message_id``（观众行）/ ``reply_to_message_id``（主播行）构成
        "主播发言回复了哪条弹幕"的关联键（互动分析数据面）。
        """

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO live_chat ("
                    "live_session_id, timestamp_ms, sender_role, sender_id, sender_name,"
                    " content, message_type, message_id, reply_to_message_id, tool_result, simulated"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        live_session_id,
                        timestamp_ms,
                        sender_role,
                        sender_id,
                        sender_name,
                        content,
                        message_type,
                        message_id,
                        reply_to_message_id,
                        tool_result,
                        1 if simulated else 0,
                    ),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def insert_gift(
        self,
        *,
        live_session_id: int,
        timestamp_ms: int,
        user_id: str,
        user_name: str,
        gift_name: str,
        gift_count: int,
        simulated: bool = False,
    ) -> int:
        """插入一条 gifts 行，返回 lastrowid。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO gifts ("
                    "live_session_id, timestamp_ms, user_id, user_name,"
                    " gift_name, gift_count, simulated"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        live_session_id,
                        timestamp_ms,
                        user_id,
                        user_name,
                        gift_name,
                        gift_count,
                        1 if simulated else 0,
                    ),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def insert_super_chat(
        self,
        *,
        live_session_id: int,
        timestamp_ms: int,
        user_id: str,
        user_name: str,
        amount: float,
        message: str,
        simulated: bool = False,
    ) -> int:
        """插入一条 super_chats 行，返回 lastrowid。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO super_chats ("
                    "live_session_id, timestamp_ms, user_id, user_name,"
                    " amount, message, simulated"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        live_session_id,
                        timestamp_ms,
                        user_id,
                        user_name,
                        amount,
                        message,
                        1 if simulated else 0,
                    ),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def insert_llm_usage(
        self,
        *,
        model_name: str,
        provider_name: str,
        request_type: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cache_hit_tokens: int = 0,
        cache_miss_tokens: int = 0,
        cost: float = 0.0,
        duration_ms: int = 0,
        profile_name: Optional[str] = None,
        assign_name: Optional[str] = None,
        live_session_id: Optional[int] = None,
        timestamp_ms: Optional[int] = None,
    ) -> int:
        """插入一条 ``llm_usage`` 调用记录，返回 lastrowid。"""
        ts = timestamp_ms if timestamp_ms is not None else now_ms()

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO llm_usage ("
                    "live_session_id, model_name, assign_name, profile_name, provider_name,"
                    " request_type, prompt_tokens, completion_tokens, total_tokens,"
                    " cache_hit_tokens, cache_miss_tokens, cost, duration_ms, timestamp_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        live_session_id,
                        model_name,
                        assign_name,
                        profile_name,
                        provider_name,
                        request_type,
                        prompt_tokens,
                        completion_tokens,
                        total_tokens,
                        cache_hit_tokens,
                        cache_miss_tokens,
                        cost,
                        duration_ms,
                        ts,
                    ),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    # -------------------- live_sessions 场次状态 --------------------
    # 场次行由 LiveSessionManager（src/modules/session/）创建与结账：一行 =
    # 一场直播（有开始/结束边界），主键 AUTOINCREMENT；房间/频道是普通属性列。
    # 本层只提供行级领域方法，不持有"当前场次"状态。

    async def insert_live_session(
        self,
        *,
        stream_id: str = "",
        platform: str = "unknown",
        started_at_ms: int,
        title: Optional[str] = None,
        source: str = "manual",
    ) -> int:
        """插入一场新场次（``ended_at_ms`` 为 NULL 即进行中），返回场次主键。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO live_sessions ("
                    "stream_id, platform, started_at_ms, title, source, updated_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?)",
                    (stream_id, platform, started_at_ms, title, source, started_at_ms),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def close_live_session(self, *, live_session_id: int, ended_at_ms: int) -> bool:
        """写入场次结束时间（幂等：已结束的场次不覆盖）。返回是否命中行。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE live_sessions SET ended_at_ms=?, updated_at_ms=? WHERE id=? AND ended_at_ms IS NULL",
                    (ended_at_ms, ended_at_ms, live_session_id),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def update_live_session_stats(
        self,
        *,
        live_session_id: int,
        heat: int,
        viewer_count: int,
        audience_total: int,
        updated_at_ms: int,
    ) -> bool:
        """更新场次实时状态（热度/计数心跳）。行不存在（如临时场次被清理）返回 False。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE live_sessions SET heat=?, viewer_count=?, audience_total=?, updated_at_ms=? WHERE id=?",
                    (heat, viewer_count, audience_total, updated_at_ms, live_session_id),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def get_live_session(self, *, live_session_id: int) -> Optional[sqlite3.Row]:
        """按场次主键查单行；未命中返回 None。"""

        def _exec() -> Optional[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return conn.execute("SELECT * FROM live_sessions WHERE id=?", (live_session_id,)).fetchone()

        return await self._run_in_executor(_exec)

    async def get_scratch_live_session(self) -> Optional[sqlite3.Row]:
        """查临时兜底场次行（source='scratch' 且未结束）；不存在返回 None。"""

        def _exec() -> Optional[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return conn.execute(
                    "SELECT * FROM live_sessions WHERE source='scratch' AND ended_at_ms IS NULL ORDER BY id LIMIT 1"
                ).fetchone()

        return await self._run_in_executor(_exec)

    async def list_dangling_live_sessions(self) -> List[sqlite3.Row]:
        """列出未结账的显式场次（ended_at_ms IS NULL 且非 scratch）。

        用于启动期收口：上次进程未正常退出的残留"进行中"场次。
        """

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return list(
                    conn.execute(
                        "SELECT * FROM live_sessions WHERE ended_at_ms IS NULL AND source != 'scratch'"
                    ).fetchall()
                )

        return await self._run_in_executor(_exec)

    async def count_session_details(self, *, live_session_id: int) -> int:
        """统计场次明细行数（live_chat + gifts + super_chats），供空场次判定。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    "SELECT ("
                    "(SELECT COUNT(*) FROM live_chat WHERE live_session_id=?) + "
                    "(SELECT COUNT(*) FROM gifts WHERE live_session_id=?) + "
                    "(SELECT COUNT(*) FROM super_chats WHERE live_session_id=?)"
                    ") AS n",
                    (live_session_id, live_session_id, live_session_id),
                ).fetchone()
                return int(row["n"]) if row else 0

        return await self._run_in_executor(_exec)

    async def delete_live_session(self, *, live_session_id: int) -> bool:
        """删除场次行并级联清除其明细数据。

        级联范围：live_chat / gifts / super_chats / topics / game_events /
        timeline_summary（均以 ``live_session_id`` 引用场次主键）。
        **不含** agenda_plan / agenda_runtime——这两张表虽带同名列，但该列
        实际存的是 agenda_id（历史约定），与场次主键无关，误删会破坏节目单。
        """

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                for table in ("live_chat", "gifts", "super_chats", "topics", "game_events", "timeline_summary"):
                    conn.execute(f"DELETE FROM {table} WHERE live_session_id=?", (live_session_id,))  # noqa: S608 表名为代码内常量
                cur = conn.execute("DELETE FROM live_sessions WHERE id=?", (live_session_id,))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def list_live_sessions(self, *, limit: int = 50) -> List[sqlite3.Row]:
        """列出场次（按开始时间倒序，附消息数），供场次列表/回看选择。"""

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return list(
                    conn.execute(
                        "SELECT s.*, ("
                        "SELECT COUNT(*) FROM live_chat c WHERE c.live_session_id = s.id"
                        ") AS message_count "
                        "FROM live_sessions s ORDER BY s.started_at_ms DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                )

        return await self._run_in_executor(_exec)

    # -------------------- 领域查询方法 --------------------
    # 表结构权威在 schema.py，本层只承接常见读路径；不要把上层业务过滤逻辑
    # 收进 store（让消费方拿到 row 后自行组装）。
    #
    # 排序不变量：所有时间序列查询以 ``timestamp_ms`` 排序，禁止依赖 INSERT 顺序。
    # 回灌语义：取"最近 N 条"必须先 DESC LIMIT，再 Python 内反转；直接 ASC LIMIT
    # 会取到最老的一批，破坏 ContextService 重启回灌的正确性。

    # list_viewer_stats 排序白名单——防 SQL 注入，禁止字符串拼接列名
    _VIEWER_ORDER_BY_WHITELIST: tuple = (
        "message_count",
        "gift_count",
        "replied_count",
        "interaction_count",
        "last_active_ms",
    )

    async def list_recent_live_chat(
        self,
        *,
        live_session_id: int,
        limit: int = 30,
        before_timestamp_ms: Optional[int] = None,
    ) -> List[sqlite3.Row]:
        """取指定场次最近的 ``limit`` 条消息，按时间**正序**返回（旧→新）。

        实现：先 ``ORDER BY timestamp_ms DESC LIMIT ?`` 拿最新窗口，再 Python 内
        ``list(reversed(...))`` 反转。``before_timestamp_ms`` 用于分页/窗口截断
        （仅取 < 该时间的消息）。
        """

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                if before_timestamp_ms is None:
                    cur = conn.execute(
                        "SELECT * FROM live_chat WHERE live_session_id=? ORDER BY timestamp_ms DESC LIMIT ?",
                        (live_session_id, limit),
                    )
                else:
                    cur = conn.execute(
                        "SELECT * FROM live_chat WHERE live_session_id=? "
                        "AND timestamp_ms < ? ORDER BY timestamp_ms DESC LIMIT ?",
                        (live_session_id, before_timestamp_ms, limit),
                    )
                rows = cur.fetchall()
            # DESC 取到的是 [新→旧]，反转回 [旧→新] 满足调用方约定
            return list(reversed(rows))

        return await self._run_in_executor(_exec)

    async def list_latest_live_chat(self, *, limit: int = 60) -> List[sqlite3.Row]:
        """取全局最近 ``limit`` 条消息（跨场次），按时间**正序**返回（旧→新）。

        启动回灌语义：重启后主播应延续"最近一段对话"，跨场次取全局最新
        窗口（上一次结束的场次天然位于窗口尾部）。实现同 ``list_recent_live_chat``：
        先 DESC LIMIT 再 Python 内反转。
        """

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "SELECT * FROM live_chat ORDER BY timestamp_ms DESC LIMIT ?",
                    (limit,),
                )
                rows = cur.fetchall()
            return list(reversed(rows))

        return await self._run_in_executor(_exec)

    async def list_messages_by_user(
        self,
        *,
        user_id: str,
        since_timestamp_ms: int,
        limit: int = 50,
    ) -> List[sqlite3.Row]:
        """按用户过滤其发言历史（时间正序）。

        ``sender_id`` 只匹配 viewer 行；assistant 行的 sender_id 是主播名，
        不会混入该查询。
        """

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "SELECT * FROM live_chat WHERE sender_id=? AND timestamp_ms >= ? ORDER BY timestamp_ms ASC LIMIT ?",
                    (user_id, since_timestamp_ms, limit),
                )
                return list(cur.fetchall())

        return await self._run_in_executor(_exec)

    async def get_viewer_stats(
        self,
        *,
        user_id: str,
    ) -> Optional[sqlite3.Row]:
        """按 user_id 查单行观众统计；未命中返回 None。"""

        def _exec() -> Optional[sqlite3.Row]:
            with self._manager.transaction() as conn:
                cur = conn.execute("SELECT * FROM viewers WHERE user_id=?", (user_id,))
                return cur.fetchone()

        return await self._run_in_executor(_exec)

    async def list_viewer_stats(
        self,
        *,
        limit: int = 100,
        order_by: str = "message_count",
    ) -> List[sqlite3.Row]:
        """列出观众统计排行。

        ``order_by`` 仅接受白名单列名（``message_count``/``gift_count``/
        ``replied_count``/``interaction_count``/``last_active_ms``），
        非法值直接 ``ValueError``——避免任何列名拼接。
        """
        if order_by not in self._VIEWER_ORDER_BY_WHITELIST:
            raise ValueError(
                f"list_viewer_stats order_by 非法: {order_by!r}，允许值: {self._VIEWER_ORDER_BY_WHITELIST}"
            )

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    f"SELECT * FROM viewers ORDER BY {order_by} DESC LIMIT ?",  # noqa: S608
                    (limit,),
                )
                return list(cur.fetchall())

        return await self._run_in_executor(_exec)

    async def upsert_viewer_message(
        self,
        *,
        user_id: str,
        user_name: str,
        timestamp_ms: int,
    ) -> None:
        """观众发言计数：``message_count`` +1，``interaction_count`` +1。"""

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO viewers("
                    "user_id, user_name, message_count, gift_count, replied_count,"
                    " interaction_count, last_active_ms"
                    ") VALUES (?, ?, 1, 0, 0, 1, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET "
                    "user_name=excluded.user_name, "
                    "message_count=message_count+1, "
                    "interaction_count=interaction_count+1, "
                    "last_active_ms=excluded.last_active_ms",
                    (user_id, user_name, timestamp_ms),
                )

        await self._run_in_executor(_exec)

    async def upsert_viewer_gift(
        self,
        *,
        user_id: str,
        user_name: str,
        timestamp_ms: int,
    ) -> None:
        """观众送礼计数：``gift_count`` +1，``interaction_count`` +1。"""

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO viewers("
                    "user_id, user_name, message_count, gift_count, replied_count,"
                    " interaction_count, last_active_ms"
                    ") VALUES (?, ?, 0, 1, 0, 1, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET "
                    "user_name=excluded.user_name, "
                    "gift_count=gift_count+1, "
                    "interaction_count=interaction_count+1, "
                    "last_active_ms=excluded.last_active_ms",
                    (user_id, user_name, timestamp_ms),
                )

        await self._run_in_executor(_exec)

    async def upsert_viewer_replied(
        self,
        *,
        user_id: str,
        timestamp_ms: int,
    ) -> None:
        """主播回复该用户计数：``replied_count`` +1，``interaction_count`` +1。

        若该 user_id 首次进入统计行（未发过言也未送过礼），以 "?" 占位 user_name
        首建——不强制外部补传用户名（用户名的权威来源是发言/礼物事件）。
        """

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO viewers("
                    "user_id, user_name, message_count, gift_count, replied_count,"
                    " interaction_count, last_active_ms"
                    ") VALUES (?, '?', 0, 0, 1, 1, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET "
                    "replied_count=replied_count+1, "
                    "interaction_count=interaction_count+1, "
                    "last_active_ms=excluded.last_active_ms",
                    (user_id, timestamp_ms),
                )

        await self._run_in_executor(_exec)

    # -------------------- 模拟器数据 CRUD（sim_personas / sim_gifts） --------------------

    # sim_personas / sim_gifts 可更新字段白名单：动态 SET 拼接前逐一校验，
    # 非法键直接拒绝（防列名注入，同 list_viewer_stats 的 order_by 白名单思路）
    _SIM_PERSONA_UPDATABLE_FIELDS = frozenset(
        {
            "user_nickname",
            "role",
            "personality",
            "speaking_style",
            "fans_medal_level",
            "guard_level",
            "context_window_size",
            "is_active",
            "messages_generated",
        }
    )
    _SIM_GIFT_UPDATABLE_FIELDS = frozenset(
        {
            "gift_name",
            "category",
            "weight",
            "data_type",
            "sc_amount_rmb",
        }
    )

    async def count_sim_personas(self) -> int:
        """返回常驻人设总数（含停用行），供启动期种子导入判断空表。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM sim_personas").fetchone()
                return int(row["n"]) if row else 0

        return await self._run_in_executor(_exec)

    async def list_sim_personas(self, *, include_inactive: bool = False) -> List[sqlite3.Row]:
        """列出常驻人设（按昵称排序）；默认排除停用行。"""

        def _exec() -> List[sqlite3.Row]:
            sql = "SELECT * FROM sim_personas"
            if not include_inactive:
                sql += " WHERE is_active=1"
            sql += " ORDER BY user_nickname ASC"
            with self._manager.transaction() as conn:
                return list(conn.execute(sql).fetchall())

        return await self._run_in_executor(_exec)

    async def insert_sim_persona(
        self,
        *,
        user_id: str,
        user_nickname: str,
        role: str,
        personality: str,
        speaking_style: str,
        fans_medal_level: int = 0,
        guard_level: int = 0,
        context_window_size: Optional[int] = None,
        is_active: bool = True,
        messages_generated: int = 0,
    ) -> int:
        """插入一条常驻人设，返回 lastrowid；``user_id`` 冲突抛 IntegrityError。"""
        now_ms = int(time.time() * 1000)

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO sim_personas("
                    "user_id, user_nickname, role, personality, speaking_style,"
                    " fans_medal_level, guard_level, context_window_size,"
                    " is_active, messages_generated, created_at_ms, updated_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        user_nickname,
                        role,
                        personality,
                        speaking_style,
                        fans_medal_level,
                        guard_level,
                        context_window_size,
                        1 if is_active else 0,
                        messages_generated,
                        now_ms,
                        now_ms,
                    ),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def update_sim_persona(self, *, user_id: str, fields: Dict[str, object]) -> bool:
        """按字段更新常驻人设（白名单校验键名），自动维护 ``updated_at_ms``。

        Returns:
            True 更新成功；False 人设不存在或无可更新字段。
        """
        if not fields:
            return False
        unknown = set(fields) - self._SIM_PERSONA_UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"update_sim_persona 非法字段: {sorted(unknown)}")
        set_sql = ", ".join(f"{key}=?" for key in fields)
        params = list(fields.values()) + [int(time.time() * 1000), user_id]

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    f"UPDATE sim_personas SET {set_sql}, updated_at_ms=? WHERE user_id=?",  # noqa: S608
                    params,
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def delete_sim_persona(self, *, user_id: str) -> bool:
        """删除常驻人设行。

        Returns:
            True 删除成功；False 人设不存在。
        """

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute("DELETE FROM sim_personas WHERE user_id=?", (user_id,))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def count_sim_gifts(self) -> int:
        """返回礼物目录条目总数，供启动期种子导入判断空表。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM sim_gifts").fetchone()
                return int(row["n"]) if row else 0

        return await self._run_in_executor(_exec)

    async def list_sim_gifts(self) -> List[sqlite3.Row]:
        """列出礼物目录（按 id 排序，保持插入顺序）。"""

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return list(conn.execute("SELECT * FROM sim_gifts ORDER BY id ASC").fetchall())

        return await self._run_in_executor(_exec)

    async def insert_sim_gift(
        self,
        *,
        gift_id: str,
        gift_name: str,
        category: str,
        weight: int = 1,
        data_type: str,
        sc_amount_rmb: Optional[int] = None,
    ) -> int:
        """插入一条礼物目录条目，返回 lastrowid；``gift_id`` 冲突抛 IntegrityError。"""
        now_ms = int(time.time() * 1000)

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO sim_gifts("
                    "gift_id, gift_name, category, weight, data_type, sc_amount_rmb,"
                    " created_at_ms, updated_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (gift_id, gift_name, category, weight, data_type, sc_amount_rmb, now_ms, now_ms),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def update_sim_gift(self, *, gift_id: str, fields: Dict[str, object]) -> bool:
        """按字段更新礼物目录条目（白名单校验键名），自动维护 ``updated_at_ms``。

        Returns:
            True 更新成功；False 礼物不存在或无可更新字段。
        """
        if not fields:
            return False
        unknown = set(fields) - self._SIM_GIFT_UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"update_sim_gift 非法字段: {sorted(unknown)}")
        set_sql = ", ".join(f"{key}=?" for key in fields)
        params = list(fields.values()) + [int(time.time() * 1000), gift_id]

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    f"UPDATE sim_gifts SET {set_sql}, updated_at_ms=? WHERE gift_id=?",  # noqa: S608
                    params,
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def delete_sim_gift(self, *, gift_id: str) -> bool:
        """删除礼物目录条目。

        Returns:
            True 删除成功；False 礼物不存在。
        """

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute("DELETE FROM sim_gifts WHERE gift_id=?", (gift_id,))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    # -------------------- 内部 --------------------

    async def _run_in_executor(self, fn, /, *args, **kwargs):
        """统一 ``asyncio.to_thread`` 防漏（store 内部所有同步调用都走这里）。"""
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
        # 1. 应用 DDL（IF NOT EXISTS 幂等，含最新列）
        with self._manager.transaction() as conn:
            conn.executescript(build_schema_sql())

        # 2. 推进版本：执行 [current+1, SCHEMA_VERSION] 区间内的迁移回调
        #    （回调原地修改、幂等），随后写入版本记录
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
                logger.info(f"SQLiteStore schema 已应用: version={SCHEMA_VERSION}（前版本={current_version}）")
            elif current_version > SCHEMA_VERSION:
                logger.warning(
                    f"SQLiteStore 数据库 schema 版本 ({current_version}) 高于代码期望 ({SCHEMA_VERSION})。"
                    f"可能是回滚到旧版本；请确认意图。"
                )
            else:
                logger.debug(f"SQLiteStore schema 已是当前版本: {SCHEMA_VERSION}")


__all__ = ["SQLiteStore", "sqlite_store", "set_default_store"]


# 引入 ManagedSQLiteConnection 仅为类型导出便利（不在 __all__）
_ = ManagedSQLiteConnection
