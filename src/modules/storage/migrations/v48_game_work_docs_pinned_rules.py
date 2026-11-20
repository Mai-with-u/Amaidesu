"""v47 → v48：``game_work_docs`` 新增 ``pinned_rules`` 持久化钉住规矩列。

背景：工作笔记（``notebook``）是 2000 字整篇覆写文档，长场次中多轮后台折叠与笔记改写容易覆盖或稀释
同伴交代的设施使用约定、领地边界与工具禁忌。新增独立的 ``pinned_rules`` JSON 字符串数组列，
按 ``(game, save_id)`` 随存档持久化保存长期钉住规矩，与工作笔记互相隔离。

幂等保证：执行前先用 ``table_exists`` 与 ``column_exists`` 检查，表缺失跳过、列已存在时不重复执行
``ALTER TABLE``，重复迁移与新建库均安全。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：为 ``game_work_docs`` 补 ``pinned_rules`` 列（默认空 JSON 数组）。"""
    if not table_exists(conn, "game_work_docs"):
        return
    if not column_exists(conn, "game_work_docs", "pinned_rules"):
        conn.execute("ALTER TABLE game_work_docs ADD COLUMN pinned_rules TEXT NOT NULL DEFAULT '[]'")
