"""v2 → v3：SimpleMemory 模块私有表（无数据变换）。

新增 ``_memory_facts``（关键词召回的事实记忆）与 ``_memory_profiles``
（人物画像）两张 ``_`` 前缀私有表及召回热路径索引（按时间、按来源）。
由 ``build_schema_sql()`` 的 DDL 自动补齐，无数据变换，本条目为显式 no-op。
"""

from __future__ import annotations

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    """无数据变换；私有表由 ``build_schema_sql()`` 的 DDL 自动补齐。"""
