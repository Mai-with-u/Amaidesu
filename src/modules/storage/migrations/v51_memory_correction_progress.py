"""v50 → v51：扩展 ``memory_corrections.verdict`` 约束以支持 ``progress``（本场进展）。

场内活承诺账本需要支持分期推进承诺（例如还了一部分材料、尚未全部还清），在保持承诺仍为 ``open``
的同时记录一条可撤回的阶段性进展。此前 ``memory_corrections.verdict`` 的 CHECK 约束只允许
``done / invalid / wrong / note``，本迁移以 SQLite 安全重建表的方式扩展为
``CHECK (verdict IN ('done', 'invalid', 'wrong', 'progress', 'note'))``，完整保留全部存量行、
自增主键、外键级联与索引。

幂等保证：执行前读取 ``sqlite_master`` 中 ``memory_corrections`` 的建表 DDL，若已包含 ``'progress'``
（新建库或已迁移库）则直接跳过不改表，重复执行零副作用。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import table_exists

_COLUMNS = (
    "id, live_session_id, target_kind, archive_line_id, commitment_id, verdict, summary,"
    " evidence_kind, evidence_quote, evidence_game_chat_id, source, prev_commitment_status,"
    " created_at_ms, reverted_at_ms, settled_disposition"
)

_MEMORY_CORRECTIONS_V51_NEW_SQL = """
CREATE TABLE memory_corrections_v51_new (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id        INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    target_kind            TEXT NOT NULL CHECK (target_kind IN ('archive', 'commitment', 'general')),
    archive_line_id        INTEGER REFERENCES archive_lines(id) ON DELETE CASCADE,
    commitment_id          INTEGER REFERENCES commitments(id) ON DELETE CASCADE,
    verdict                TEXT NOT NULL CHECK (verdict IN ('done', 'invalid', 'wrong', 'progress', 'note')),
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
""".strip()

_MEMORY_CORRECTIONS_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_memory_corrections_session ON memory_corrections(live_session_id, created_at_ms)"
)


def migrate(conn: sqlite3.Connection) -> None:
    """原地幂等扩展 ``memory_corrections.verdict`` CHECK 约束以允许 ``'progress'``。"""
    if not table_exists(conn, "memory_corrections"):
        return
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_corrections'").fetchone()
    ddl = str(row[0] or "") if row is not None else ""
    if "'progress'" in ddl:
        # 新建库或已完成 v51 重建的库直接跳过，避免无意义锁表或改表
        return

    # 安全重建表：创建新表 -> 按列原样拷入全部存量行（保留 id 与外键） -> 替换原表 -> 重建索引
    conn.execute("DROP TABLE IF EXISTS memory_corrections_v51_new")
    conn.execute(_MEMORY_CORRECTIONS_V51_NEW_SQL)
    conn.execute(
        f"INSERT INTO memory_corrections_v51_new ({_COLUMNS}) SELECT {_COLUMNS} FROM memory_corrections"  # noqa: S608 列为代码内常量
    )
    conn.execute("DROP TABLE memory_corrections")
    conn.execute("ALTER TABLE memory_corrections_v51_new RENAME TO memory_corrections")
    conn.execute(_MEMORY_CORRECTIONS_INDEX_SQL)


__all__ = ["migrate"]
