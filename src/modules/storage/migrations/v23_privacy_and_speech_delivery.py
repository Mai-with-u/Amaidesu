"""v22 → v23：台词送达标记、观众正式场来访计数与回填、不记录名单表。

1. ``live_chat`` 补三列：``utterance_id``（主播台词关联键）、``dropped_at_ms``
   （播放队列确知未送达观众面前的时刻，空 = 已送达）、``memory_refs``（点名引用的记忆编号），
   并建 ``utterance_id`` 非空部分唯一索引（索引放在迁移回调里建，兼容旧库先跑建库语句再跑回调的顺序）。
2. ``viewers`` 补四列：``sessions_count``（来过几场正式场）、``last_session_id``
   （上次计入来访场次的正式场主键）、``prev_seen_ms``（本场之前最近活跃时刻）、
   ``forget_requested_at_ms``（宽限删除请求时刻）。对存量真实观众按历史弹幕回填
   ``sessions_count`` 与 ``last_session_id``（只数观众弹幕行、非模拟、所属场次 ``kind='live'``）。
3. 补建 ``forget_list`` 表（HMAC 摘要主键）。

幂等保证：列添加均先经 ``column_exists`` 检查；部分索引与新表使用 ``IF NOT EXISTS``；
来访计数回填仅在 ``sessions_count`` 列本次新增、或存量行尚未回填（计数为 0 且 ``last_session_id`` 为空）时写入。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists
from src.modules.storage.schema import _FORGET_LIST_SQL


def migrate(conn: sqlite3.Connection) -> None:
    """应用 v23 结构增量与历史来访计数回填（幂等）。"""
    if not column_exists(conn, "live_chat", "utterance_id"):
        conn.execute("ALTER TABLE live_chat ADD COLUMN utterance_id TEXT")
    if not column_exists(conn, "live_chat", "dropped_at_ms"):
        conn.execute("ALTER TABLE live_chat ADD COLUMN dropped_at_ms INTEGER")
    if not column_exists(conn, "live_chat", "memory_refs"):
        conn.execute("ALTER TABLE live_chat ADD COLUMN memory_refs TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_live_chat_utterance"
        " ON live_chat(utterance_id) WHERE utterance_id IS NOT NULL"
    )

    had_sessions_count = column_exists(conn, "viewers", "sessions_count")
    if not had_sessions_count:
        conn.execute("ALTER TABLE viewers ADD COLUMN sessions_count INTEGER NOT NULL DEFAULT 0")
    if not column_exists(conn, "viewers", "last_session_id"):
        conn.execute("ALTER TABLE viewers ADD COLUMN last_session_id INTEGER")
    if not column_exists(conn, "viewers", "prev_seen_ms"):
        conn.execute("ALTER TABLE viewers ADD COLUMN prev_seen_ms INTEGER")
    if not column_exists(conn, "viewers", "forget_requested_at_ms"):
        conn.execute("ALTER TABLE viewers ADD COLUMN forget_requested_at_ms INTEGER")

    # 从历史真实观众弹幕回填正式场来访计数与最近来访场次（仅针对尚未计数的非模拟观众，重复执行结果不变）。
    # 极旧的中间形状可能缺场次表或 live_chat 的角色/类型列：这时无法判定"正式场真弹幕"，保持计数为默认 0，
    # 不阻断迁移（缺列的库本就没有可用的历史观众弹幕可数）。
    can_backfill = (
        table_exists(conn, "live_sessions")
        and all(
            column_exists(conn, "live_chat", column)
            for column in ("sender_role", "message_type", "simulated", "viewer_id")
        )
        and all(column_exists(conn, "live_sessions", column) for column in ("kind", "started_at_ms", "ended_at_ms"))
    )
    if not can_backfill:
        conn.executescript(_FORGET_LIST_SQL)
        return

    conn.execute(
        """
        UPDATE viewers
        SET
            sessions_count = COALESCE((
                SELECT COUNT(DISTINCT lc.live_session_id)
                FROM live_chat lc
                JOIN live_sessions ls ON ls.id = lc.live_session_id
                WHERE lc.viewer_id = viewers.id
                  AND lc.sender_role = 'viewer'
                  AND lc.message_type = 'danmaku'
                  AND lc.simulated = 0
                  AND ls.kind = 'live'
            ), 0),
            last_session_id = (
                SELECT lc.live_session_id
                FROM live_chat lc
                JOIN live_sessions ls ON ls.id = lc.live_session_id
                WHERE lc.viewer_id = viewers.id
                  AND lc.sender_role = 'viewer'
                  AND lc.message_type = 'danmaku'
                  AND lc.simulated = 0
                  AND ls.kind = 'live'
                ORDER BY ls.started_at_ms DESC, ls.id DESC
                LIMIT 1
            )
        WHERE simulated = 0 AND sessions_count = 0 AND last_session_id IS NULL
        """
    )

    conn.executescript(_FORGET_LIST_SQL)
