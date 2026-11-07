"""v34 → v35：游戏事件行记下发起方（委派账用）。

委派账落在 ``game_events`` 里（``delegation_*`` 类型的行），原先没记这件委派是谁发起的：
主播自己派的与运营在 WebUI 直派的分不开，续播时运营的调试指令也会被认领成她手头的事，
她会说"重启前我在做<运营的调试指令>"。这一版给 ``game_events`` 加一列 ``initiator``：
委派账行写发起的 Agent 注册名（运营直派写 operator），其余游戏事件行为空串。

存量行不回填：旧的委派账行当时没记，空串即"发起方不明"，续播不认领它们。
列已存在（新库或已迁移过）时跳过，没有这张表时什么都不做。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：游戏事件补"发起方"一列，旧行为空串。"""
    if table_exists(conn, "game_events") and not column_exists(conn, "game_events", "initiator"):
        conn.execute("ALTER TABLE game_events ADD COLUMN initiator TEXT NOT NULL DEFAULT ''")


__all__ = ["migrate"]
