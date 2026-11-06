"""v30 → v31：观众候选表与档案行的来源列。

观众定下、她当场接受却没复述的事（起的名字、定的设定、造的梗），以及她说"欠着的"、账上却找不到
的承诺，整理时列成候选，人批准了才进记忆。这一版只建存储：

- ``viewer_candidates``：一条候选一行。``kind`` 是 ``viewer_defined``（观众定下的）或
  ``claimed_missing``（她说欠着的）；挂在两条台词记录上——观众那条弹幕（``viewer_chat_id``，
  她说欠着的一类只在她是回应某条弹幕时才有）和她那句回应（``reply_chat_id``），两条都按外键级联：
  删观众、忘了我、按句删弹幕或台词、删场次时候选随之删除。``status`` 是待审 / 已批 / 已驳回；
  批准后生成的档案行或承诺记在 ``archive_line_id`` / ``commitment_id``（那一行再被删时置空）。
- ``archive_lines.source``：这一行是主播自己说的（``streamer``，旧行一律是）还是批准的观众候选
  （``viewer``）；``archive_lines.viewer_chat_id``：观众来源的行挂着的那条弹幕，同样按外键级联删除。

建表语句在这里留一份定稿副本：建表汇总以后改动，不影响这一版迁移做过的事。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists

# (列, 列定义)：旧行一律是主播自己说的、不挂观众弹幕
_ARCHIVE_COLUMNS = (
    ("source", "TEXT NOT NULL DEFAULT 'streamer'"),
    ("viewer_chat_id", "INTEGER REFERENCES live_chat(id) ON DELETE CASCADE"),
)

_CANDIDATES_SQL = (
    """
CREATE TABLE IF NOT EXISTS viewer_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    title           TEXT NOT NULL,
    viewer_chat_id  INTEGER REFERENCES live_chat(id) ON DELETE CASCADE,
    viewer_quote    TEXT NOT NULL DEFAULT '',
    reply_chat_id   INTEGER NOT NULL REFERENCES live_chat(id) ON DELETE CASCADE,
    reply_quote     TEXT NOT NULL,
    game            TEXT,
    save_id         TEXT NOT NULL DEFAULT '',
    save_name       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at_ms   INTEGER NOT NULL,
    decided_at_ms   INTEGER,
    archive_line_id INTEGER REFERENCES archive_lines(id) ON DELETE SET NULL,
    commitment_id   INTEGER REFERENCES commitments(id) ON DELETE SET NULL
)
""",
    "CREATE INDEX IF NOT EXISTS idx_viewer_candidates_session ON viewer_candidates(live_session_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_viewer_candidates_viewer_chat ON viewer_candidates(viewer_chat_id)",
    "CREATE INDEX IF NOT EXISTS idx_viewer_candidates_reply_chat ON viewer_candidates(reply_chat_id)",
)

_ARCHIVE_VIEWER_INDEX = "CREATE INDEX IF NOT EXISTS idx_archive_lines_viewer_chat ON archive_lines(viewer_chat_id)"


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：档案行补来源与观众弹幕两列 → 建观众候选表与索引。"""
    if table_exists(conn, "archive_lines"):
        for column, definition in _ARCHIVE_COLUMNS:
            if not column_exists(conn, "archive_lines", column):
                # 带外键的列只能以空值为默认补进来（SQLite 的限制），旧行正好都不挂观众弹幕
                conn.execute(f"ALTER TABLE archive_lines ADD COLUMN {column} {definition}")  # noqa: S608 常量列定义
        conn.execute(_ARCHIVE_VIEWER_INDEX)
    if table_exists(conn, "live_sessions") and table_exists(conn, "live_chat"):
        for statement in _CANDIDATES_SQL:
            conn.execute(statement)
