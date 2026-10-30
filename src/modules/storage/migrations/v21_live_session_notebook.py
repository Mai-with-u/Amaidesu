"""v20 → v21：``live_sessions`` 加本场备忘录全文列 ``notebook_text``。

备忘录是主播在直播中经工具写下的本场结论（答应了谁什么、哪个梗定下来了），
收播整理时作为回顾与长期笔记的原料。此前它只活在进程内存里：直播中直接关掉
应用（Agent 先停、收播事件后到）或进程崩溃，下次启动补整理时备忘录已经没了，
本场写下的约定就进不了长期笔记。改为每次写备忘录时同步写进场次行，整理时
按场次主键读回。

存量行补空串：旧场次本来就没有落盘的备忘录。列已存在（新库或已迁移过）时跳过。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：补 ``notebook_text`` 列，存量行为空串。"""
    if not table_exists(conn, "live_sessions"):
        return
    if column_exists(conn, "live_sessions", "notebook_text"):
        return
    conn.execute("ALTER TABLE live_sessions ADD COLUMN notebook_text TEXT NOT NULL DEFAULT ''")


__all__ = ["migrate"]
