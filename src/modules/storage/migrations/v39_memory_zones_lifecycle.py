"""v38 → v39：记忆分区生命周期存储——承诺最近提起场次与了结来源列、场内更正表、整理断点表。

这一版补齐跨场承诺过期、场内当场更正与下播整理断点续跑三项生命周期所需的存储：

- ``commitments.last_mentioned_session_id``：承诺最近一次被提起的场次号；旧行回填为登记场次
  ``created_session_id``，此后本场点名引用或整理认出重申时推进，用于判断承诺是否搁了很久。
- ``commitments.close_source``：了结或作废来源（空串 未了结 / ``settle`` 下播整理 /
  ``correction`` 场内更正 / ``human`` 人工操作）；旧行按 ``close_chat_id`` 是否非空回填为
  ``settle`` 或 ``human``，支持人工撤回场内更正而不误伤人工结论。
- ``memory_corrections``：场内更正表（主播决策工具或运营递话对本场简报行或承诺的当场纠正）。
- ``settle_checkpoints``：下播整理每次模型调用解析成功后的输入指纹与输出缓存表。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists

_MEMORY_CORRECTIONS_SQL = (
    """
CREATE TABLE IF NOT EXISTS memory_corrections (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id        INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    target_kind            TEXT NOT NULL CHECK (target_kind IN ('archive', 'commitment', 'general')),
    archive_line_id        INTEGER REFERENCES archive_lines(id) ON DELETE CASCADE,
    commitment_id          INTEGER REFERENCES commitments(id) ON DELETE CASCADE,
    verdict                TEXT NOT NULL CHECK (verdict IN ('done', 'invalid', 'wrong', 'note')),
    summary                TEXT NOT NULL,
    evidence_kind          TEXT NOT NULL CHECK (evidence_kind IN ('game_receipt', 'player_chat', 'operator', 'self_seen')),
    evidence_quote         TEXT NOT NULL DEFAULT '',
    evidence_game_chat_id  INTEGER REFERENCES game_chat(id) ON DELETE CASCADE,
    source                 TEXT NOT NULL CHECK (source IN ('streamer', 'operator')),
    prev_commitment_status TEXT NOT NULL DEFAULT '',
    created_at_ms          INTEGER NOT NULL,
    reverted_at_ms         INTEGER,
    settled_disposition    TEXT NOT NULL DEFAULT ''
)
""",
    "CREATE INDEX IF NOT EXISTS idx_memory_corrections_session ON memory_corrections(live_session_id, created_at_ms)",
)

_SETTLE_CHECKPOINTS_SQL = """
CREATE TABLE IF NOT EXISTS settle_checkpoints (
    live_session_id INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    step            TEXT NOT NULL,
    fingerprint     TEXT NOT NULL,
    output_json     TEXT NOT NULL,
    saved_at_ms     INTEGER NOT NULL,
    PRIMARY KEY (live_session_id, step)
)
"""


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：为 commitments 补两列并回填旧行，创建场内更正表与整理断点表。"""
    if table_exists(conn, "commitments"):
        if not column_exists(conn, "commitments", "last_mentioned_session_id"):
            conn.execute("ALTER TABLE commitments ADD COLUMN last_mentioned_session_id INTEGER")
        # 旧行（及新添列后尚未回填的登记行）用登记场次回填最近提起场次
        conn.execute(
            "UPDATE commitments SET last_mentioned_session_id = created_session_id"
            " WHERE last_mentioned_session_id IS NULL AND created_session_id IS NOT NULL"
        )
        if not column_exists(conn, "commitments", "close_source"):
            conn.execute("ALTER TABLE commitments ADD COLUMN close_source TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "UPDATE commitments SET close_source = 'settle'"
            " WHERE status = 'closed' AND close_chat_id IS NOT NULL AND close_source = ''"
        )
        conn.execute(
            "UPDATE commitments SET close_source = 'human'"
            " WHERE status IN ('closed', 'void') AND close_chat_id IS NULL AND close_source = ''"
        )

    if table_exists(conn, "live_sessions"):
        conn.execute(_SETTLE_CHECKPOINTS_SQL)
        if (
            table_exists(conn, "archive_lines")
            and table_exists(conn, "commitments")
            and table_exists(conn, "game_chat")
        ):
            for statement in _MEMORY_CORRECTIONS_SQL:
                conn.execute(statement)
