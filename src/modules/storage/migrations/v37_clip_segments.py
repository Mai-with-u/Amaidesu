"""v36 → v37：切片段存储——AI 提议与运营手调的切片时段，含切割状态与产物路径。

这一版只建存储，不改任何读写行为：

- ``clip_segments``：一个切片候选时段一行。``origin`` 区分 AI 提议（ai）与运营
  手建（manual）；``start_ms``/``end_ms`` 用墙钟毫秒（场时间线坐标，与
  ``live_sessions.started_at_ms`` 同一坐标系），切成文件内偏移是切割时的换算；
  ``status`` 从提议（suggested）走到产物落盘（done）或失败（failed）。
  切割产物的复切/删段等操作直接改行，无历史账。

旧行无（新表）。建表语句在这里留一份定稿副本：建表汇总以后改动，不影响这一版迁移做过的事。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import table_exists

_CLIP_SEGMENTS_SQL = (
    """
CREATE TABLE IF NOT EXISTS clip_segments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    origin          TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('ai', 'manual')),
    start_ms        INTEGER NOT NULL,
    end_ms          INTEGER NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    reason          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'suggested'
                    CHECK (status IN ('suggested', 'confirmed', 'cutting', 'done', 'failed', 'dismissed')),
    output_path     TEXT NOT NULL DEFAULT '',
    error_message   TEXT NOT NULL DEFAULT '',
    created_at_ms   INTEGER NOT NULL,
    updated_at_ms   INTEGER NOT NULL,
    CHECK (end_ms >= start_ms)
)
""",
    "CREATE INDEX IF NOT EXISTS idx_clip_segments_session ON clip_segments(live_session_id, start_ms)",
)


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：建切片段表与场次索引（外键引用 live_sessions，缺它就跳过）。"""
    if not table_exists(conn, "live_sessions"):
        return
    for statement in _CLIP_SEGMENTS_SQL:
        conn.execute(statement)
