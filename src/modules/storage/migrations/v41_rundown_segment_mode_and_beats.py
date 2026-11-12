"""v40 → v41：流程单环节表与实例环节快照表新增说话模式（mode）与提纲要点（beats_json）列。

杂谈场与日常游戏场的开场闲聊段需要按环节指定说话模式（play / talk）及分段提纲要点列表：
- ``mode``：可空文本列（仅允许 NULL / 'play' / 'talk'），旧行默认 NULL（按当前游戏在线状态自动判定）。
- ``beats_json``：非空 JSON 字符串列表，旧行默认 ``'[]'``（无提纲要点，不触发未聊要点早退护栏）。

通过 ``column_exists`` 检查保证对新建库与已迁移库重复执行幂等。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists

_TABLES = ("rundown_segments", "rundown_instance_segments")


def migrate(conn: sqlite3.Connection) -> None:
    """原地幂等为流程单环节双表补齐 mode 与 beats_json 列。"""
    for table in _TABLES:
        if not table_exists(conn, table):
            continue
        if not column_exists(conn, table, "mode"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN mode TEXT CHECK (mode IS NULL OR mode IN ('play', 'talk'))")
        if not column_exists(conn, table, "beats_json"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN beats_json TEXT NOT NULL DEFAULT '[]'")
