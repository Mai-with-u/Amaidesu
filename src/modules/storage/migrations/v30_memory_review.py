"""v29 → v30：记忆页人工审核的存储——审核状态、人工锁定、放回快照标记与改动记录。

整理照旧自动生效、不等人审（否则哪场没审她那场就失忆）；人只在事后改。这一版只建存储，
不改任何读写行为：

- ``live_sessions.reviewed_at_ms``：运营者标"已审"的时刻，空表示待审；
- ``archive_lines.human_locked``：运营者改过标题、稳定标记、触发词、地点，或这一行是运营者新增的，
  之后的整理不能去掉或替换它；
- ``archive_snapshot_lines.pinned``：运营者放回快照或新增的行，取舍时排在稳定的事之前；
- ``memory_edits``：每次操作一行（场次、对象、动作、改前改后、时刻），与档案同寿命——场次被删时级联
  删除；行被删时由触发器清空改前改后的文本，只留动作与时刻。

旧行一律未审、未锁、未放回。建表语句在这里留一份定稿副本：建表汇总以后改动，不影响这一版迁移做过的事。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists

# (表, 列, 列定义)：旧行取默认值——未审、未锁、未放回
_COLUMNS = (
    ("live_sessions", "reviewed_at_ms", "INTEGER"),
    ("archive_lines", "human_locked", "INTEGER NOT NULL DEFAULT 0"),
    ("archive_snapshot_lines", "pinned", "INTEGER NOT NULL DEFAULT 0"),
)

_EDITS_SQL = (
    """
CREATE TABLE IF NOT EXISTS memory_edits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    target          TEXT NOT NULL,
    archive_line_id INTEGER,
    action          TEXT NOT NULL,
    before_text     TEXT NOT NULL DEFAULT '',
    after_text      TEXT NOT NULL DEFAULT '',
    at_ms           INTEGER NOT NULL
)
""",
    "CREATE INDEX IF NOT EXISTS idx_memory_edits_session ON memory_edits(live_session_id, at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_memory_edits_line ON memory_edits(archive_line_id)",
)

# 行被删时清空改动记录里的文本（记录本身留着动作与时刻）
_LINE_DELETED_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS trg_memory_edits_line_deleted AFTER DELETE ON archive_lines BEGIN
    UPDATE memory_edits SET before_text = '', after_text = '' WHERE archive_line_id = OLD.id;
END
"""


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：补审核状态、人工锁定与放回标记三列 → 建改动记录表 → 建删行清文本的触发器。"""
    for table, column, definition in _COLUMNS:
        # 表不在（极早期的库）就跳过：建表汇总会按最新形状建出来
        if table_exists(conn, table) and not column_exists(conn, table, column):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")  # noqa: S608 常量表名列名
    if table_exists(conn, "live_sessions"):
        for statement in _EDITS_SQL:
            conn.execute(statement)
        if table_exists(conn, "archive_lines"):
            conn.execute(_LINE_DELETED_TRIGGER)
