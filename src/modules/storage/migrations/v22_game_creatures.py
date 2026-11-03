"""v21 → v22：新增游戏生物名册表 ``game_creatures``（无数据变换）。

游戏 Agent 记住的同伴（角色自己的生物、主播起过名的生物）此前只在内存里：Agent 重建或进程重启，
主播给女仆起的名字、她死在哪一场就都丢了，下一场又当成陌生人。改为按"游戏 + 世界 + 身份键"
一行落库，跨场次记得。新表由 ``build_schema_sql()`` 的 IF NOT EXISTS DDL 自动补齐，没有存量
数据要搬，本条目为显式 no-op。
"""

from __future__ import annotations

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    """无数据变换；新表由 ``build_schema_sql()`` 的 DDL 自动补齐。"""
