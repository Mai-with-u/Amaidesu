"""v28 → v29：记忆检索的派生索引、线索卡记录，以及档案行与承诺的触发词、地点列。

直播记忆的回想与被动想起改成两路检索：全文（FTS5，中文按两个字一组切开后建索引）与向量（嵌入）。
这一版只建存储，不改任何读写行为：

- ``memory_vectors_*``：档案行、承诺、她播出的台词、游戏事件各一张向量表，挂外键级联，来源行删了向量
  跟着没；
- ``memory_fts_*``：同四类的全文虚表。虚表挂不了外键，靠触发器跟着来源行删除与改正文，并打开 FTS5
  自己的 secure-delete，删掉的词元不留在影子表里；
- ``memory_cue_log``：每张推出去的线索卡一行（谁触发的、分数、用上没有），只记条目编号不记观众身份；
- ``archive_lines.keywords`` / ``archive_lines.place`` / ``commitments.keywords``：被动想起用的触发词与
  挂靠的地点，旧行一律空（之后的整理才写）。

派生表全空，由索引任务按正本补算；补算前回想照旧可用。建表语句在这里留一份定稿副本：
建表汇总以后改动，不影响这一版迁移做过的事。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists

# 四类来源：(向量表, 向量表主键列, 全文表, 来源表, 来源表正文列)
_SOURCES = (
    ("memory_vectors_archive", "archive_line_id", "memory_fts_archive", "archive_lines", "text, quote"),
    ("memory_vectors_commitment", "commitment_id", "memory_fts_commitment", "commitments", "what, quote"),
    ("memory_vectors_speech", "chat_id", "memory_fts_speech", "live_chat", "content, dropped_at_ms"),
    ("memory_vectors_event", "event_id", "memory_fts_event", "game_events", "message"),
)

# 触发器名用的来源短名，与建表汇总里的名字一致（IF NOT EXISTS 才能幂等）
_SHORT_NAMES = {
    "archive_lines": "archive",
    "commitments": "commitment",
    "live_chat": "speech",
    "game_events": "event",
}

_CUE_LOG_SQL = (
    """
CREATE TABLE IF NOT EXISTS memory_cue_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    pushed_at_ms    INTEGER NOT NULL,
    card_kind       TEXT NOT NULL,
    archive_line_id INTEGER REFERENCES archive_lines(id) ON DELETE CASCADE,
    commitment_id   INTEGER REFERENCES commitments(id) ON DELETE CASCADE,
    sources         TEXT NOT NULL DEFAULT '[]',
    detail          TEXT NOT NULL DEFAULT '{}',
    self_triggered  INTEGER NOT NULL DEFAULT 0,
    used_at_ms      INTEGER
)
""",
    "CREATE INDEX IF NOT EXISTS idx_memory_cue_log_session ON memory_cue_log(live_session_id, pushed_at_ms)",
)


def _source_statements(vectors: str, key: str, fts: str, source: str, columns: str) -> tuple[str, ...]:
    """一类来源的向量表、全文虚表与两条触发器（删除跟着删；正文被改时连同向量一起删，等重算）。"""
    short = _SHORT_NAMES[source]
    return (
        f"""
CREATE TABLE IF NOT EXISTS {vectors} (
    {key} INTEGER PRIMARY KEY REFERENCES {source}(id) ON DELETE CASCADE,
    model     TEXT NOT NULL,
    dim       INTEGER NOT NULL,
    text_hash TEXT NOT NULL,
    vector    BLOB NOT NULL
)
""",
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {fts} USING fts5(tokens)",
        f"""
CREATE TRIGGER IF NOT EXISTS trg_memory_index_{short}_deleted AFTER DELETE ON {source} BEGIN
    DELETE FROM {fts} WHERE rowid = OLD.id;
END
""",
        f"""
CREATE TRIGGER IF NOT EXISTS trg_memory_index_{short}_edited AFTER UPDATE OF {columns} ON {source} BEGIN
    DELETE FROM {fts} WHERE rowid = OLD.id;
    DELETE FROM {vectors} WHERE {key} = OLD.id;
END
""",
    )


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：补触发词与地点列 → 建派生索引与触发器 → 打开全文表的 secure-delete → 建线索卡记录。"""
    if table_exists(conn, "archive_lines"):
        if not column_exists(conn, "archive_lines", "keywords"):
            conn.execute("ALTER TABLE archive_lines ADD COLUMN keywords TEXT NOT NULL DEFAULT '[]'")
        if not column_exists(conn, "archive_lines", "place"):
            conn.execute("ALTER TABLE archive_lines ADD COLUMN place TEXT NOT NULL DEFAULT ''")
    if table_exists(conn, "commitments") and not column_exists(conn, "commitments", "keywords"):
        conn.execute("ALTER TABLE commitments ADD COLUMN keywords TEXT NOT NULL DEFAULT '[]'")
    for vectors, key, fts, source, columns in _SOURCES:
        # 来源表不在（极早期的库）就跳过这一类：触发器要引用它
        if not table_exists(conn, source):
            continue
        for statement in _source_statements(vectors, key, fts, source, columns):
            conn.execute(statement)
        # FTS5 的 secure-delete 是表级持久设置：删掉的词元当场从影子表抹掉，不等合并
        conn.execute(f"INSERT INTO {fts}({fts}, rank) VALUES('secure-delete', 1)")
    if table_exists(conn, "live_sessions"):
        for statement in _CUE_LOG_SQL:
            conn.execute(statement)
