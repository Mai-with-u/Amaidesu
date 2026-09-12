"""v1 → v2：模拟器运行时数据入库（无数据变换）。

新增 sim_personas（常驻观众人设）与 sim_gifts（礼物目录）两张运行时表，
供模拟器三模式的运行时数据落库。新表由 ``build_schema_sql()`` 的
IF NOT EXISTS DDL 自动补齐，无存量数据变换，本条目为显式 no-op。
"""

from __future__ import annotations

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    """无数据变换；新表由 ``build_schema_sql()`` 的 DDL 自动补齐。"""
