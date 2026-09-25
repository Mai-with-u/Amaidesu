"""v9 → v10：付费明细三表补全 + 平台身份键 + 付费统计 + 记忆表重构。

一次迁移涵盖五块表变更（各自独立幂等，合并升版避免多次迁移）：

1. **付费明细（gifts / super_chats / guards）**
   - ``gifts``：``gift_count`` 改名 ``quantity``；加金额（``unit_price`` /
     ``total_price`` / ``paid_price`` / ``currency``）、身份快照
     （``guard_level`` / ``fans_medal_*``）、连击（``combo_*``）、
     盲盒（``blind_gift_id``）、``gift_id`` / ``msg_id`` / ``raw_data``
   - ``super_chats``：重建表——``amount``（REAL，人民币元）改为
     ``total_price``（INTEGER，金瓜子，1 元 = 1000 金瓜子，存量数据 ×1000
     换算）；加 ``currency`` / ``start_time`` / ``end_time`` / 身份快照 /
     ``message_id`` / ``raw_data``
   - ``guards``：新建购买事件表（每次上舰/续费一条，周期存原值）
2. **平台身份键**：``live_chat`` / ``gifts`` / ``super_chats`` / ``viewers``
   加 ``platform`` 列；``viewers`` 重建表，唯一约束从 ``user_id`` 换为
   ``(platform, user_id)`` 复合键。存量行的 platform 回填 ``'bilibili'``
   （历史数据均产自 B 站链路，console 调试行无法区分、一并归入）
3. **付费统计**：``viewers`` 加 ``paid_count`` / ``paid_amount``（金瓜子）
4. **记忆表重构**：``DROP TABLE _memory_facts``（扁平事实副本，两源均有
   权威表，冗余废弃）；私有表机制随之废除
5. **观众画像表**：新建 ``viewer_facts``（事实原料）与 ``viewer_profiles``
   （LLM 压缩画像，``last_compressed_at_ms`` 为增量压缩水位）

guards / viewer_facts / viewer_profiles 为新建表，由建库 DDL 直接创建，
回调仅处理存量库的数据表变换。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists

# 存量行 platform 回填值：历史数据均产自 B 站链路（本项目唯一生产平台）
_LEGACY_PLATFORM = "bilibili"

_GIFTS_ADD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("platform", "TEXT NOT NULL DEFAULT ''"),
    ("gift_id", "INTEGER NOT NULL DEFAULT 0"),
    ("unit_price", "INTEGER NOT NULL DEFAULT 0"),
    ("total_price", "INTEGER NOT NULL DEFAULT 0"),
    ("paid_price", "INTEGER NOT NULL DEFAULT 0"),
    ("currency", "TEXT NOT NULL DEFAULT ''"),
    ("guard_level", "INTEGER NOT NULL DEFAULT 0"),
    ("fans_medal_level", "INTEGER NOT NULL DEFAULT 0"),
    ("fans_medal_name", "TEXT NOT NULL DEFAULT ''"),
    ("combo_id", "TEXT NOT NULL DEFAULT ''"),
    ("combo_count", "INTEGER NOT NULL DEFAULT 0"),
    ("combo_gift", "INTEGER NOT NULL DEFAULT 0"),
    ("blind_gift_id", "INTEGER NOT NULL DEFAULT 0"),
    ("msg_id", "TEXT NOT NULL DEFAULT ''"),
    ("raw_data", "TEXT"),
)


def _add_columns(conn: sqlite3.Connection, table: str, columns: tuple[tuple[str, str], ...]) -> None:
    """逐列 ADD COLUMN（列存在即跳过，幂等）。"""
    for name, ddl in columns:
        if not column_exists(conn, table, name):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")  # noqa: S608 列名/类型为代码内常量


def _migrate_gifts(conn: sqlite3.Connection) -> None:
    """gifts：gift_count 改名 quantity + 补全金额/身份快照/连击/raw_data 列。"""
    if not table_exists(conn, "gifts"):
        return
    if not column_exists(conn, "gifts", "quantity") and column_exists(conn, "gifts", "gift_count"):
        conn.execute("ALTER TABLE gifts RENAME COLUMN gift_count TO quantity")
    _add_columns(conn, "gifts", _GIFTS_ADD_COLUMNS)


def _migrate_super_chats(conn: sqlite3.Connection) -> None:
    """super_chats：重建表——amount（元，REAL）换算为 total_price（金瓜子，INTEGER）+ 补全列。

    SQLite 的 REAL 列亲和性会把整数值存为浮点，金额统一金瓜子的整数语义
    必须重建列。存量数据 ×1000 换算（1 元 = 1000 金瓜子，ROUND 兜底浮点误差）。
    """
    if not table_exists(conn, "super_chats"):
        return
    if column_exists(conn, "super_chats", "total_price"):
        # 已是新形态（含部分升级的中间态）：只补缺失列
        _add_columns(
            conn,
            "super_chats",
            (
                ("platform", "TEXT NOT NULL DEFAULT ''"),
                ("currency", "TEXT NOT NULL DEFAULT ''"),
                ("start_time", "INTEGER NOT NULL DEFAULT 0"),
                ("end_time", "INTEGER NOT NULL DEFAULT 0"),
                ("guard_level", "INTEGER NOT NULL DEFAULT 0"),
                ("fans_medal_level", "INTEGER NOT NULL DEFAULT 0"),
                ("fans_medal_name", "TEXT NOT NULL DEFAULT ''"),
                ("message_id", "TEXT NOT NULL DEFAULT ''"),
                ("raw_data", "TEXT"),
            ),
        )
        return

    conn.execute(
        """
        CREATE TABLE super_chats_v10_new (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            live_session_id   INTEGER NOT NULL,
            timestamp_ms      INTEGER NOT NULL,
            platform          TEXT NOT NULL DEFAULT '',
            user_id           TEXT NOT NULL,
            user_name         TEXT NOT NULL,
            message           TEXT NOT NULL,
            total_price       INTEGER NOT NULL DEFAULT 0,
            currency          TEXT NOT NULL DEFAULT '',
            start_time        INTEGER NOT NULL DEFAULT 0,
            end_time          INTEGER NOT NULL DEFAULT 0,
            guard_level       INTEGER NOT NULL DEFAULT 0,
            fans_medal_level  INTEGER NOT NULL DEFAULT 0,
            fans_medal_name   TEXT NOT NULL DEFAULT '',
            message_id        TEXT NOT NULL DEFAULT '',
            raw_data          TEXT,
            simulated         INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        INSERT INTO super_chats_v10_new (
            id, live_session_id, timestamp_ms, platform, user_id, user_name,
            message, total_price, currency, simulated
        )
        SELECT id, live_session_id, timestamp_ms, ?, user_id, user_name,
               message, CAST(ROUND(COALESCE(amount, 0) * 1000) AS INTEGER), ?, simulated
        FROM super_chats
        """,
        (_LEGACY_PLATFORM, "bilibili_gold_coin"),
    )
    conn.execute("DROP TABLE super_chats")
    conn.execute("ALTER TABLE super_chats_v10_new RENAME TO super_chats")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_super_chats_user ON super_chats(user_id)")


def _migrate_live_chat(conn: sqlite3.Connection) -> None:
    """live_chat：加 platform 列。"""
    if not table_exists(conn, "live_chat"):
        return
    _add_columns(conn, "live_chat", (("platform", "TEXT NOT NULL DEFAULT ''"),))


def _migrate_viewers(conn: sqlite3.Connection) -> None:
    """viewers：重建表——唯一约束 user_id → (platform, user_id) 复合键 + 付费统计列。

    SQLite 无法就地修改唯一约束，必须重建。存量行 platform 归入
    ``'bilibili'``；付费统计列对存量行置 0（派生值，可由明细重算）。
    """
    if not table_exists(conn, "viewers"):
        return
    if not column_exists(conn, "viewers", "platform"):
        conn.execute(
            """
            CREATE TABLE viewers_v10_new (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                platform            TEXT NOT NULL DEFAULT '',
                user_id             TEXT NOT NULL,
                user_name           TEXT NOT NULL,
                message_count       INTEGER NOT NULL DEFAULT 0,
                gift_count          INTEGER NOT NULL DEFAULT 0,
                replied_count       INTEGER NOT NULL DEFAULT 0,
                interaction_count   INTEGER NOT NULL DEFAULT 0,
                paid_count          INTEGER NOT NULL DEFAULT 0,
                paid_amount         INTEGER NOT NULL DEFAULT 0,
                last_active_ms      INTEGER NOT NULL,
                UNIQUE(platform, user_id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO viewers_v10_new (
                id, platform, user_id, user_name, message_count, gift_count,
                replied_count, interaction_count, paid_count, paid_amount, last_active_ms
            )
            SELECT id, ?, user_id, user_name, message_count, gift_count,
                   replied_count, interaction_count, 0, 0, last_active_ms
            FROM viewers
            """,
            (_LEGACY_PLATFORM,),
        )
        conn.execute("DROP TABLE viewers")
        conn.execute("ALTER TABLE viewers_v10_new RENAME TO viewers")
        return
    # 已有 platform 列（中间态）：只补付费统计列
    _add_columns(
        conn,
        "viewers",
        (
            ("paid_count", "INTEGER NOT NULL DEFAULT 0"),
            ("paid_amount", "INTEGER NOT NULL DEFAULT 0"),
        ),
    )


def _migrate_sim_gifts(conn: sqlite3.Connection) -> None:
    """sim_gifts：加 unit_price 列（模拟器礼物标价，金瓜子；SC 走 sc_amount_rmb）。"""
    if not table_exists(conn, "sim_gifts"):
        return
    _add_columns(conn, "sim_gifts", (("unit_price", "INTEGER NOT NULL DEFAULT 0"),))


def _drop_memory_facts(conn: sqlite3.Connection) -> None:
    """删除 SimpleMemory 扁平事实私有表（两源均有权威表，冗余副本废弃）。"""
    conn.execute("DROP TABLE IF EXISTS _memory_facts")


def _backfill_platform(conn: sqlite3.Connection) -> None:
    """存量明细行 platform 回填：空值行归入历史生产平台。"""
    for table in ("live_chat", "gifts", "super_chats"):
        if table_exists(conn, table) and column_exists(conn, table, "platform"):
            conn.execute(f"UPDATE {table} SET platform = ? WHERE platform = ''", (_LEGACY_PLATFORM,))  # noqa: S608 表名为代码内常量


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：新建库已是最新形态，各步骤用列存在性检查保证重复执行安全。"""
    _migrate_gifts(conn)
    _migrate_super_chats(conn)
    _migrate_live_chat(conn)
    _migrate_viewers(conn)
    _migrate_sim_gifts(conn)
    _drop_memory_facts(conn)
    _backfill_platform(conn)


__all__ = ["migrate"]
