"""迁移回调共用小工具。"""

from __future__ import annotations

import sqlite3


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """检查表是否存在（sqlite_master）。"""
    rows = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchall()
    return len(rows) > 0


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """检查表列是否存在（PRAGMA table_info）。"""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # noqa: S608 表名为代码内常量
    return any(row[1] == column for row in rows)


__all__ = ["table_exists", "column_exists"]
