"""v25 → v26：游戏事件、承诺与场次档案记下所属的游戏存档。

换了游戏存档（例如 Minecraft 开了新世界）后，主播还会把旧世界里的建筑、进度与答应过的
游戏里的事当成眼前的事：这几张表只记了"哪个游戏"，没记"哪个存档"。

- ``game_events`` 加 ``save_id`` / ``save_name``：事件发生时角色所在的存档，由发布方填；
- ``commitments`` 加 ``save_id`` / ``save_name``：挂了游戏的承诺在哪个存档里答应的；
- ``archive_lines`` 加 ``game`` / ``save_id`` / ``save_name``：这一行写的是哪个游戏世界里的事。

存量行不回填：旧数据当时就没记存档，空串即"存档不明"，读方按不明处理（照给并注明不确定）。
列已存在（新库或已迁移过）时跳过，没有这张表时什么都不做。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists

# 表 → 要补的列（列名, 列定义）；存档两列空串即"不分存档或存档不明"
_COLUMNS = {
    "game_events": (
        ("save_id", "TEXT NOT NULL DEFAULT ''"),
        ("save_name", "TEXT NOT NULL DEFAULT ''"),
    ),
    "commitments": (
        ("save_id", "TEXT NOT NULL DEFAULT ''"),
        ("save_name", "TEXT NOT NULL DEFAULT ''"),
    ),
    "archive_lines": (
        ("game", "TEXT"),
        ("save_id", "TEXT NOT NULL DEFAULT ''"),
        ("save_name", "TEXT NOT NULL DEFAULT ''"),
    ),
}


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：给三张表补存档列，存量行留空串（档案行的游戏留空）。"""
    for table, columns in _COLUMNS.items():
        if not table_exists(conn, table):
            continue
        for name, definition in columns:
            if not column_exists(conn, table, name):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")  # noqa: S608 表名列名为代码内常量


__all__ = ["migrate"]
