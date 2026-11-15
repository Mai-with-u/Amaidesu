"""v43 → v44：场次行补两列——人手写的备注，与模型写的这场概览。

场次这一行原本只有标题可写（``title``），标题是"这场叫什么"，不是"这场怎么样"。
两件事都缺落处，于是各补一列：

- ``notes``：运营者手写的场次备注（自由文本，空 = 没写）；
- ``overview``：模型写的这场概览（"这场发生了什么"，给人快速回看与查找用；
  空 = 还没生成）。它不进任何记忆表，与下播整理写出的场次档案是两回事。

两列同属一次升版（一个字段一版太浪费），存量行都按空读。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：为场次行补 notes 与 overview 两列（默认空）。"""
    if not table_exists(conn, "live_sessions"):
        return
    if not column_exists(conn, "live_sessions", "notes"):
        conn.execute("ALTER TABLE live_sessions ADD COLUMN notes TEXT")
    if not column_exists(conn, "live_sessions", "overview"):
        conn.execute("ALTER TABLE live_sessions ADD COLUMN overview TEXT")


__all__ = ["migrate"]
