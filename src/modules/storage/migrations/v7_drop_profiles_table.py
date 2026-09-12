"""v6 → v7：删除 SimpleMemory 人物画像私有表。

画像读写面（读写方法 + 数据模型）已整体移除，生产代码零引用；
该表只被本模块的 SimpleMemory 读写，直接 DROP 回收空间。新建库的
``build_schema_sql()`` 已不含该表 DDL，DROP IF EXISTS 仅处理存量库。
"""

from __future__ import annotations

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：DROP IF EXISTS 处理存量库。"""
    conn.execute("DROP TABLE IF EXISTS _memory_profiles")


__all__ = ["migrate"]
