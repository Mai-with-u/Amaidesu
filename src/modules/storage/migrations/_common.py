"""迁移回调共用小工具。"""

from __future__ import annotations

import sqlite3


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """检查表列是否存在（PRAGMA table_info）。"""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # noqa: S608 表名为代码内常量
    return any(row[1] == column for row in rows)


__all__ = ["column_exists"]
