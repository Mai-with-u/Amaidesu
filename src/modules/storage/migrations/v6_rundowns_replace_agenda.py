"""v5 → v6：rundowns 表替代 agenda_plan / agenda_runtime 两表。

v5 时代的 agenda 存储链路未在运行时接线，两表保证为空；直接 DROP。
rundowns 表由 ``_RUNDOWNS_SQL``（IF NOT EXISTS 幂等）建立，对新库
与已升级库都安全——新库的 ``build_schema_sql()`` 已不含旧表 DDL，此处
DROP IF EXISTS 仅为处理已升级库。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.schema import _RUNDOWNS_SQL


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：DROP 旧表 + 幂等建立 rundowns。"""
    conn.execute("DROP TABLE IF EXISTS agenda_plan")
    conn.execute("DROP TABLE IF EXISTS agenda_runtime")
    conn.executescript(_RUNDOWNS_SQL)


__all__ = ["migrate"]
