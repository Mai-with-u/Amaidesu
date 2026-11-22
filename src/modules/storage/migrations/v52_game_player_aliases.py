"""为 ``game_players`` 增加纯游戏侧常用称呼列 ``aliases``。

联机同伴的游戏账号名与口头常用简称往往不同，缺失稳定别名锚点时，规划器与游戏 Agent 容易把不同
玩家串成同一人，下播整理也难以把口头称呼归属回对应 UUID。本迁移为 ``game_players`` 增补
``aliases TEXT NOT NULL DEFAULT '[]'``（JSON 字符串列表，仅由运营者手动维护，与隐私隔离表
``viewer_player_links`` 彻底无关）。

幂等保证：通过 ``table_exists`` 与 ``column_exists`` 检查列是否已存在，新建库或已迁移库直接跳过，
重复执行零副作用。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists


def migrate(conn: sqlite3.Connection) -> None:
    """原地幂等为 ``game_players`` 补上 ``aliases`` 列（默认空 JSON 数组 ``'[]'``）。"""
    if table_exists(conn, "game_players") and not column_exists(conn, "game_players", "aliases"):
        conn.execute("ALTER TABLE game_players ADD COLUMN aliases TEXT NOT NULL DEFAULT '[]'")


__all__ = ["migrate"]
