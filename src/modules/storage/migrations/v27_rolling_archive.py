"""v26 → v27：档案行加"出自哪一场"（``from_session_id``）与"是否长期"（``lasting``）两列。

这两列服务于把上一份档案里仍然成立的行复制进本场名下的滚动写法；v28 改为快照只引用原行，
把这一版整理出的副本换成引用后删掉两列（见 ``v28_archive_snapshots``）。存量行不回填，
列已存在时跳过，没有这张表时什么都不做。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists

# 要补的列（列名, 列定义）
_COLUMNS = (
    ("from_session_id", "INTEGER"),
    ("lasting", "INTEGER NOT NULL DEFAULT 0"),
)


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：给 archive_lines 补两列，存量行留空（出自本行所在场次、不算长期）。"""
    if not table_exists(conn, "archive_lines"):
        return
    for name, definition in _COLUMNS:
        if not column_exists(conn, "archive_lines", name):
            conn.execute(f"ALTER TABLE archive_lines ADD COLUMN {name} {definition}")  # noqa: S608 列名为代码内常量
    conn.execute("CREATE INDEX IF NOT EXISTS idx_archive_lines_from_session ON archive_lines(from_session_id)")


__all__ = ["migrate"]
