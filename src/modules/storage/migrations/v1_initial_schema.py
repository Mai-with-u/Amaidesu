"""v0 → v1：初始建表（无数据变换）。

建立首批业务表（live_sessions / live_chat / gifts / super_chats / topics /
viewers / agenda_plan / agenda_runtime / game_events / timeline_summary /
llm_usage）与版本表 schema_migrations。该版本的表早已存在于所有现实库中，
无任何数据变换，本条目为显式 no-op——严格 +1 纪律要求 1..SCHEMA_VERSION
每版都在注册表内。
"""

from __future__ import annotations

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    """无数据变换；表结构由 ``build_schema_sql()`` 的 DDL 自动补齐。"""
