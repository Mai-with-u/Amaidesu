"""v42 → v43：为场次档案与滚动档案快照补名场面标记列 highlight。

长线游戏存档播过几十场后，和观众共同经历的大工程落成、首次首领击杀或全服爆笑翻车既不属于静态取名，
也不该隔两三场就随普通事件滑出快照；纯杂谈场次眼下未进游戏存档时，同样需要挑出跨存档共同经历供聊天回忆。
本版为 ``archive_lines`` 与 ``archive_snapshot_lines`` 各补 ``highlight INTEGER NOT NULL DEFAULT 0``，
存量旧行默认按非名场面兼容读取。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：为档案行与快照行补 highlight 列（默认 0）。"""
    if table_exists(conn, "archive_lines") and not column_exists(conn, "archive_lines", "highlight"):
        conn.execute("ALTER TABLE archive_lines ADD COLUMN highlight INTEGER NOT NULL DEFAULT 0")
    if table_exists(conn, "archive_snapshot_lines") and not column_exists(conn, "archive_snapshot_lines", "highlight"):
        conn.execute("ALTER TABLE archive_snapshot_lines ADD COLUMN highlight INTEGER NOT NULL DEFAULT 0")
