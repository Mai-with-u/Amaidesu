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
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.modules.logging import get_logger
from src.modules.storage.connection import ManagedSQLiteConnection, SQLiteConnectionManager
from src.modules.storage.schema import (
    SCHEMA_MIGRATIONS,
    SCHEMA_VERSION,
    build_schema_sql,
    list_expected_tables,
)
from src.modules.time_utils import now_ms

if TYPE_CHECKING:
    from src.agents.streamer.rundown.rundown import Rundown


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

        用于启动健康检查；不通过即阻止启动。
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
    # 三表均带 simulated 贯穿列，详见 schema.py "命名硬规则"。
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
    # 场次行由 LiveSessionManager 创建与结账：一行 =
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
        """更新场次实时状态（热度/计数心跳）。行不存在（如默认场次被清理）返回 False。"""

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

    async def list_dangling_live_sessions(self) -> List[sqlite3.Row]:
        """列出未结账的显式场次（ended_at_ms IS NULL）。

        用于启动期收口：上次进程未正常退出的残留"进行中"场次。
        """

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return list(conn.execute("SELECT * FROM live_sessions WHERE ended_at_ms IS NULL").fetchall())

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

    async def list_live_sessions(
        self,
        *,
        limit: int = 50,
        source: Optional[str] = None,
        title_keyword: Optional[str] = None,
    ) -> List[sqlite3.Row]:
        """列出场次（附消息数），供场次列表/回看选择。

        排序：按开始时间倒序。无显式场次期间不创建兜底行；列表只含历史
        显式场次。
        筛选：``source`` 精确匹配来源；``title_keyword`` 对 title 做包含匹配。

        Args:
            limit: 最多返回条数
            source: 来源过滤（manual / replay / legacy）；None 不过滤
            title_keyword: 标题关键字；None 或空串不过滤
        """

        conditions: List[str] = []
        params: List[object] = []
        if source:
            conditions.append("s.source=?")
            params.append(source)
        if title_keyword:
            conditions.append("s.title LIKE ?")
            params.append(f"%{title_keyword}%")
        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return list(
                    conn.execute(
                        "SELECT s.*, ("
                        "SELECT COUNT(*) FROM live_chat c WHERE c.live_session_id = s.id"
                        ") AS message_count "
                        f"FROM live_sessions s {where_clause} "
                        "ORDER BY s.started_at_ms DESC LIMIT ?",
                        params,
                    ).fetchall()
                )

        return await self._run_in_executor(_exec)

    # -------------------- 领域查询方法 --------------------
    # 表结构权威在 schema.py，本层只承接常见读路径；不要把上层业务过滤逻辑
    # 收进 store（让消费方拿到 row 后自行组装）。
    #
    # 排序不变量：所有时间序列查询以 ``timestamp_ms`` 排序，禁止依赖 INSERT 顺序。
    # 取"最近 N 条"必须先 DESC LIMIT，再 Python 内反转；直接 ASC LIMIT 会取到
    # 最老的一批，破坏时间线回看的正确性。

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
        sender_role: Optional[str] = None,
    ) -> List[sqlite3.Row]:
        """取指定场次最近的 ``limit`` 条消息，按时间**正序**返回（旧→新）。

        实现：先 ``ORDER BY timestamp_ms DESC LIMIT ?`` 拿最新窗口，再 Python 内
        ``list(reversed(...))`` 反转。``before_timestamp_ms`` 用于分页/窗口截断
        （仅取 < 该时间的消息）。``sender_role`` 可选过滤发送方角色
        （``"viewer"``=观众 / ``"assistant"``=主播）。
        """

        def _exec() -> List[sqlite3.Row]:
            clauses = ["live_session_id=?"]
            params: List[Any] = [live_session_id]
            if sender_role is not None:
                clauses.append("sender_role=?")
                params.append(sender_role)
            if before_timestamp_ms is not None:
                clauses.append("timestamp_ms<?")
                params.append(before_timestamp_ms)
            params.append(limit)
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "SELECT * FROM live_chat WHERE " + " AND ".join(clauses) + " ORDER BY timestamp_ms DESC LIMIT ?",
                    params,
                )
                rows = cur.fetchall()
            # DESC 取到的是 [新→旧]，反转回 [旧→新] 满足调用方约定
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

    # -------------------- 事件历史（event_history） --------------------

    async def insert_event(
        self,
        *,
        record_id: str,
        event_name: str,
        timestamp_ms: int,
        level: str = "info",
        source: str = "",
        summary: str = "",
        payload_json: str = "{}",
    ) -> int:
        """插入一条事件历史行，返回 lastrowid。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO event_history ("
                    "record_id, event_name, timestamp_ms, level, source, summary, payload"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (record_id, event_name, timestamp_ms, level, source, summary, payload_json),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def list_event_dates(self, event_name: str) -> List[str]:
        """列出指定事件名有记录的本地日期（``YYYY-MM-DD``，时间正序）。"""
        rows = await self.execute(
            "SELECT DISTINCT date(timestamp_ms / 1000, 'unixepoch', 'localtime') AS d"
            " FROM event_history WHERE event_name = ? ORDER BY d",
            (event_name,),
        )
        return [str(row["d"]) for row in rows if row["d"] is not None]

    async def get_day_events(self, date_str: str, *, event_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """读取指定本地日期的事件历史行（时间正序）；``event_name`` 为 None 时取全部事件。

        返回 dict 行（record_id / event_name / timestamp_ms / level / source / summary / payload），
        payload 为原始 JSON 字符串，由调用方解析。
        """
        if event_name is not None:
            sql = (
                "SELECT record_id, event_name, timestamp_ms, level, source, summary, payload"
                " FROM event_history WHERE event_name = ?"
                " AND date(timestamp_ms / 1000, 'unixepoch', 'localtime') = ?"
                " ORDER BY timestamp_ms, seq"
            )
            params: Any = (event_name, date_str)
        else:
            sql = (
                "SELECT record_id, event_name, timestamp_ms, level, source, summary, payload"
                " FROM event_history"
                " WHERE date(timestamp_ms / 1000, 'unixepoch', 'localtime') = ?"
                " ORDER BY timestamp_ms, seq"
            )
            params = (date_str,)
        rows = await self.execute(sql, params)
        return [dict(row) for row in rows]

    # -------------------- LLM 请求历史（llm_requests） --------------------

    async def insert_llm_request(
        self,
        *,
        request_id: str,
        timestamp_ms: int,
        client_type: str = "",
        model_name: str = "",
        request_params_json: Optional[str] = None,
        response_content: Optional[str] = None,
        reasoning_content: Optional[str] = None,
        tool_calls_json: Optional[str] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cost: float = 0.0,
        success: bool = True,
        error: Optional[str] = None,
        latency_ms: int = 0,
    ) -> bool:
        """插入一条请求历史行；``request_id`` 冲突时忽略（幂等）。

        Returns:
            True 实际插入；False 已存在被忽略。
        """

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO llm_requests ("
                    "request_id, timestamp_ms, client_type, model_name, request_params, response_content,"
                    " reasoning_content, tool_calls, prompt_tokens, completion_tokens, total_tokens,"
                    " cost, success, error, latency_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        request_id,
                        timestamp_ms,
                        client_type,
                        model_name,
                        request_params_json,
                        response_content,
                        reasoning_content,
                        tool_calls_json,
                        prompt_tokens,
                        completion_tokens,
                        total_tokens,
                        cost,
                        1 if success else 0,
                        error,
                        latency_ms,
                    ),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    @staticmethod
    def _llm_request_where(
        *,
        client_type: Optional[str],
        model_name: Optional[str],
        start_time: Optional[int],
        end_time: Optional[int],
        success_only: Optional[bool],
    ) -> "tuple[str, List[Any]]":
        """组装 llm_requests 查询的 WHERE 子句（子句全部为代码内常量）。"""
        clauses: List[str] = []
        params: List[Any] = []
        if client_type:
            clauses.append("client_type = ?")
            params.append(client_type)
        if model_name:
            clauses.append("model_name = ?")
            params.append(model_name)
        if start_time is not None:
            clauses.append("timestamp_ms >= ?")
            params.append(start_time)
        if end_time is not None:
            clauses.append("timestamp_ms <= ?")
            params.append(end_time)
        if success_only is not None:
            clauses.append("success = ?")
            params.append(1 if success_only else 0)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    async def query_llm_requests(
        self,
        *,
        client_type: Optional[str] = None,
        model_name: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        success_only: Optional[bool] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        """按条件分页查询请求历史（时间倒序），返回 ``{"total", "rows"}``（原始 dict 行）。"""
        where, params = self._llm_request_where(
            client_type=client_type,
            model_name=model_name,
            start_time=start_time,
            end_time=end_time,
            success_only=success_only,
        )

        def _exec() -> Dict[str, Any]:
            with self._manager.transaction() as conn:
                total = int(
                    conn.execute(f"SELECT COUNT(*) AS n FROM llm_requests{where}", tuple(params)).fetchone()["n"]  # noqa: S608 子句为代码内常量
                )
                offset = max(0, (page - 1) * page_size)
                rows = conn.execute(
                    f"SELECT * FROM llm_requests{where} ORDER BY timestamp_ms DESC LIMIT ? OFFSET ?",  # noqa: S608 子句为代码内常量
                    (*params, page_size, offset),
                ).fetchall()
                return {"total": total, "rows": [dict(row) for row in rows]}

        return await self._run_in_executor(_exec)

    async def get_llm_request_by_id(self, request_id: str) -> Optional[Dict[str, Any]]:
        """按 request_id 取单条请求历史，未命中返回 None。"""
        rows = await self.execute("SELECT * FROM llm_requests WHERE request_id = ?", (request_id,))
        return dict(rows[0]) if rows else None

    async def delete_llm_requests_before(self, *, before_date: Optional[str] = None) -> int:
        """删除请求历史；``before_date``（本地日 YYYY-MM-DD）为 None 时清空全部，返回删除行数。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                if before_date is None:
                    cur = conn.execute("DELETE FROM llm_requests")
                else:
                    cutoff_ms = int(datetime.strptime(before_date, "%Y-%m-%d").timestamp() * 1000)
                    cur = conn.execute("DELETE FROM llm_requests WHERE timestamp_ms < ?", (cutoff_ms,))
                return int(cur.rowcount or 0)

        return await self._run_in_executor(_exec)

    async def llm_request_available_dates(self) -> List[str]:
        """列出有请求历史记录的本地日期（降序）。"""
        rows = await self.execute(
            "SELECT DISTINCT date(timestamp_ms / 1000, 'unixepoch', 'localtime') AS d FROM llm_requests ORDER BY d DESC"
        )
        return [str(row["d"]) for row in rows if row["d"] is not None]

    async def llm_request_statistics(
        self,
        *,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> Dict[str, Any]:
        """聚合请求历史统计：总体指标 + 按模型 + 按客户端类型（一次方法三次查询）。"""
        where, params = self._llm_request_where(
            client_type=None,
            model_name=None,
            start_time=start_time,
            end_time=end_time,
            success_only=None,
        )

        def _exec() -> Dict[str, Any]:
            with self._manager.transaction() as conn:
                overall = conn.execute(
                    "SELECT COUNT(*) AS total,"
                    " COALESCE(SUM(success), 0) AS success_count,"
                    " COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,"
                    " COALESCE(SUM(completion_tokens), 0) AS completion_tokens,"
                    " COALESCE(SUM(total_tokens), 0) AS total_tokens,"
                    " COALESCE(SUM(cost), 0) AS total_cost,"
                    " COALESCE(AVG(latency_ms), 0) AS avg_latency"
                    f" FROM llm_requests{where}",  # noqa: S608 子句为代码内常量
                    tuple(params),
                ).fetchone()
                model_rows = conn.execute(
                    "SELECT model_name, COUNT(*) AS count,"
                    " COALESCE(SUM(total_tokens), 0) AS total_tokens,"
                    " COALESCE(SUM(cost), 0) AS total_cost"
                    f" FROM llm_requests{where} GROUP BY model_name",  # noqa: S608 子句为代码内常量
                    tuple(params),
                ).fetchall()
                client_rows = conn.execute(
                    f"SELECT client_type, COUNT(*) AS count FROM llm_requests{where} GROUP BY client_type",  # noqa: S608 子句为代码内常量
                    tuple(params),
                ).fetchall()
                return {
                    "overall": dict(overall) if overall else {},
                    "by_model": [dict(row) for row in model_rows],
                    "by_client": [dict(row) for row in client_rows],
                }

        return await self._run_in_executor(_exec)

    # -------------------- 流程单（rundowns） --------------------
    # 整场流程单整体读写（segments_json 一次性存整段列表，不建子表）。
    # 序列化与反序列化由 ``Rundown`` / ``RundownSegment`` 数据模型承担，store
    # 不解析 JSON 内部形状。``created_at_ms`` / ``updated_at_ms`` 由本层维护。

    @staticmethod
    def _serialize_rundown_segments(rundown: Rundown) -> str:
        """将 ``rundown.segments`` 列表序列化为 JSON 字符串（``rundown_id`` / ``title`` 走独立列）。"""
        return json.dumps([seg.model_dump() for seg in rundown.segments], ensure_ascii=False)

    @staticmethod
    def _deserialize_rundown(row: sqlite3.Row) -> Rundown:
        """从 rundowns 行重建 :class:`Rundown`（逐段 ``RundownSegment.model_validate``）。"""
        # 函数体内 import 限 5 种情形之一——此处属"循环 import 规避"：
        # store 顶层导入 rundown 会触发 src.agents.streamer/__init__.py 装配链
        # （StreamerAgent → Planner → memory → 本 store）的循环。
        from src.agents.streamer.rundown.rundown import Rundown, RundownSegment  # noqa: PLC0415

        segments_data = json.loads(str(row["segments_json"]))
        segments = [RundownSegment.model_validate(item) for item in segments_data]
        return Rundown(
            rundown_id=str(row["id"]),
            title=str(row["title"]),
            segments=segments,
        )

    async def upsert_rundown(self, rundown: Rundown) -> None:
        """按 ``id`` 主键插入或更新流程单；``created_at_ms`` 仅首次插入写入、``updated_at_ms`` 每次刷新为 ``now_ms()``。"""
        ts = now_ms()
        segments_json = self._serialize_rundown_segments(rundown)

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO rundowns (id, title, segments_json, created_at_ms, updated_at_ms)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(id) DO UPDATE SET"
                    " title=excluded.title,"
                    " segments_json=excluded.segments_json,"
                    " updated_at_ms=excluded.updated_at_ms",
                    (rundown.rundown_id, rundown.title, segments_json, ts, ts),
                )

        await self._run_in_executor(_exec)

    async def get_rundown(self, rundown_id: str) -> Optional[Rundown]:
        """按 ``id`` 取单条流程单；未命中返回 ``None``。"""

        def _exec() -> Optional[Rundown]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    "SELECT id, title, segments_json, created_at_ms, updated_at_ms FROM rundowns WHERE id=?",
                    (rundown_id,),
                ).fetchone()
                if row is None:
                    return None
                return self._deserialize_rundown(row)

        return await self._run_in_executor(_exec)

    async def list_rundowns(self) -> List[Rundown]:
        """列出全部流程单，按 ``created_at_ms`` 升序。"""

        def _exec() -> List[Rundown]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT id, title, segments_json, created_at_ms, updated_at_ms"
                    " FROM rundowns ORDER BY created_at_ms ASC, id ASC"
                ).fetchall()
                return [self._deserialize_rundown(row) for row in rows]

        return await self._run_in_executor(_exec)

    async def delete_rundown(self, rundown_id: str) -> bool:
        """按 ``id`` 删除流程单；行不存在返回 ``False``。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute("DELETE FROM rundowns WHERE id=?", (rundown_id,))
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
                logger.info(f"SQLiteStore schema 已应用: version={SCHEMA_VERSION}（前版本={current_version}）")
            elif current_version > SCHEMA_VERSION:
                logger.warning(
                    f"SQLiteStore 数据库 schema 版本 ({current_version}) 高于代码期望 ({SCHEMA_VERSION})。"
                    f"可能是回滚到旧版本；请确认意图。"
                )
            else:
                logger.debug(f"SQLiteStore schema 已是当前版本: {SCHEMA_VERSION}")

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


__all__ = ["SQLiteStore", "sqlite_store", "set_default_store"]


# 引入 ManagedSQLiteConnection 仅为类型导出便利（不在 __all__）
_ = ManagedSQLiteConnection
