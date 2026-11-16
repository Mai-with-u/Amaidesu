"""v44 → v45：幂等补齐场次行的 notes 与 overview 两列。

迁移机制隐含契约"同一版本号内容不可变"：版本号一经真库执行记录，调度器不再重跑。
半成品迁移若在开发中途被执行占用了版本号，其后续补全的内容变更将永不落地，且差异
静默存在。本迁移以此为教训：对 ``live_sessions`` 的 ``notes`` 与 ``overview`` 两列
各自做存在性检查后补列，让停在任意中间态（两列缺一或全缺）的库都能幂等补齐，
不依赖"库里一定停在某个特定形态"的假设。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：为场次行补 notes 与 overview 两列（默认空），缺哪列补哪列。"""
    if not table_exists(conn, "live_sessions"):
        return
    if not column_exists(conn, "live_sessions", "notes"):
        conn.execute("ALTER TABLE live_sessions ADD COLUMN notes TEXT")
    if not column_exists(conn, "live_sessions", "overview"):
        conn.execute("ALTER TABLE live_sessions ADD COLUMN overview TEXT")


__all__ = ["migrate"]
