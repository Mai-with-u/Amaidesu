"""v39 → v40：新增游戏工作文档表 ``game_work_docs``。

游戏 Agent 的工作笔记（notebook）与待办（todos）此前只留在进程内存里，重启即清零。
新增 ``game_work_docs`` 按 ``(game, save_id)`` 一存档一行覆盖落库，跨重启保留；
``updated_session_id`` 外键指向 ``live_sessions(id)``（删场次时置 NULL，文档本身保留）。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import table_exists

_GAME_WORK_DOCS_DDL = """
CREATE TABLE IF NOT EXISTS game_work_docs (
    game               TEXT NOT NULL,
    save_id            TEXT NOT NULL,
    save_name          TEXT NOT NULL DEFAULT '',
    notebook           TEXT NOT NULL DEFAULT '',
    todos              TEXT NOT NULL DEFAULT '[]',
    updated_session_id INTEGER REFERENCES live_sessions(id) ON DELETE SET NULL,
    updated_at_ms      INTEGER NOT NULL,
    PRIMARY KEY (game, save_id)
)
"""


def migrate(conn: sqlite3.Connection) -> None:
    """原地幂等建 ``game_work_docs`` 表（缺基础场次表的极简测试夹具跳过）。"""
    if not table_exists(conn, "live_sessions"):
        return
    conn.execute(_GAME_WORK_DOCS_DDL)
