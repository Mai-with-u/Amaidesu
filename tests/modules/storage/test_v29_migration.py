"""v28 → v29 迁移：记忆检索的派生索引、线索卡记录，档案行与承诺的触发词、地点列。

派生索引的删除口径全靠存储层自己保证：向量表外键级联、全文虚表靠触发器，正文被改时连同向量一起删掉
等重算。这里用 v28 形状的最小 DDL 造前置形状后直接调 ``migrate(conn)``，再用全新库走一遍启动路径。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v29_memory_index import migrate as v29_migrate

# v28 形状的最小 DDL：只留四类来源表与场次表里迁移和触发器用得到的列
_V28_SCHEMA = """
CREATE TABLE live_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_ms INTEGER NOT NULL
);
CREATE TABLE live_chat (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL,
    sender_role     TEXT NOT NULL,
    content         TEXT NOT NULL,
    dropped_at_ms   INTEGER
);
CREATE TABLE commitments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    what          TEXT NOT NULL,
    quote         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'open',
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);
CREATE TABLE archive_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    text             TEXT NOT NULL,
    quote            TEXT NOT NULL,
    evidence_chat_id INTEGER NOT NULL,
    game             TEXT,
    save_id          TEXT NOT NULL DEFAULT '',
    save_name        TEXT NOT NULL DEFAULT ''
);
CREATE TABLE game_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL,
    game            TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    message         TEXT NOT NULL,
    timestamp_ms    INTEGER NOT NULL
);
"""

# 四类来源：(来源表, 插入一行的语句, 改正文的语句, 向量表, 向量表主键列, 全文表)
_SOURCES = (
    (
        "archive_lines",
        "INSERT INTO archive_lines(live_session_id, text, quote, evidence_chat_id) VALUES (1, '猫取名芝麻', '就叫芝麻', 1)",
        "UPDATE archive_lines SET text = '猫取名年糕' WHERE id = ?",
        "memory_vectors_archive",
        "archive_line_id",
        "memory_fts_archive",
    ),
    (
        "commitments",
        "INSERT INTO commitments(what, quote, created_at_ms, updated_at_ms) VALUES ('唱首歌', '下次唱首歌', 1, 1)",
        "UPDATE commitments SET quote = '下次唱两首' WHERE id = ?",
        "memory_vectors_commitment",
        "commitment_id",
        "memory_fts_commitment",
    ),
    (
        "live_chat",
        "INSERT INTO live_chat(live_session_id, sender_role, content) VALUES (1, 'assistant', '芝麻又趴在熔炉边上')",
        "UPDATE live_chat SET dropped_at_ms = 5 WHERE id = ?",
        "memory_vectors_speech",
        "chat_id",
        "memory_fts_speech",
    ),
    (
        "game_events",
        "INSERT INTO game_events(live_session_id, game, event_type, message, timestamp_ms)"
        " VALUES (1, 'minecraft', 'milestone', '一位观众送来面包', 1)",
        "UPDATE game_events SET message = '一位观众送来蛋糕' WHERE id = ?",
        "memory_vectors_event",
        "event_id",
        "memory_fts_event",
    ),
)


@pytest.fixture()
def conn() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    # 生产连接统一打开外键；向量表与线索卡记录的级联删除靠它
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_V28_SCHEMA)
    connection.execute("INSERT INTO live_sessions(id, started_at_ms) VALUES (1, 1000)")
    try:
        yield connection
    finally:
        connection.close()


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {row[1]: str(row[4]) for row in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608 测试常量表名


def _index_row(conn: sqlite3.Connection, vectors: str, key: str, fts: str, source_id: int) -> None:
    """给一条来源行补上全文行与向量行，模拟索引任务已经算过它。"""
    conn.execute(f"INSERT INTO {fts}(rowid, tokens) VALUES (?, '芝麻 麻又')", (source_id,))  # noqa: S608
    conn.execute(
        f"INSERT INTO {vectors}({key}, model, dim, text_hash, vector) VALUES (?, 'm', 2, 'h', x'0000')",  # noqa: S608
        (source_id,),
    )


def _indexed(conn: sqlite3.Connection, vectors: str, key: str, fts: str, source_id: int) -> tuple[int, int]:
    fts_rows = conn.execute(f"SELECT COUNT(*) FROM {fts} WHERE rowid = ?", (source_id,)).fetchone()[0]  # noqa: S608
    vector_rows = conn.execute(
        f"SELECT COUNT(*) FROM {vectors} WHERE {key} = ?",  # noqa: S608
        (source_id,),
    ).fetchone()[0]
    return int(fts_rows), int(vector_rows)


def test_adds_keyword_and_place_columns_with_empty_defaults(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO archive_lines(live_session_id, text, quote, evidence_chat_id) VALUES (1, 't', 'q', 1)")
    conn.execute("INSERT INTO commitments(what, created_at_ms, updated_at_ms) VALUES ('w', 1, 1)")

    v29_migrate(conn)

    assert {"keywords", "place"} <= set(_columns(conn, "archive_lines"))
    assert "keywords" in _columns(conn, "commitments")
    line = conn.execute("SELECT keywords, place FROM archive_lines").fetchone()
    assert (line["keywords"], line["place"]) == ("[]", ""), "旧行一律没有触发词、不挂地点，等之后的整理写"
    assert conn.execute("SELECT keywords FROM commitments").fetchone()["keywords"] == "[]"


@pytest.mark.parametrize(("source", "insert", "_edit", "vectors", "key", "fts"), _SOURCES)
def test_deleting_a_source_row_drops_its_index_rows(
    conn: sqlite3.Connection, source: str, insert: str, _edit: str, vectors: str, key: str, fts: str
) -> None:
    """删单行、按句删台词、删场次时，派生索引不能留下这一行的词元或向量。"""
    v29_migrate(conn)
    source_id = int(conn.execute(insert).lastrowid)
    _index_row(conn, vectors, key, fts, source_id)

    conn.execute(f"DELETE FROM {source} WHERE id = ?", (source_id,))  # noqa: S608

    assert _indexed(conn, vectors, key, fts, source_id) == (0, 0)


@pytest.mark.parametrize(("_source", "insert", "edit", "vectors", "key", "fts"), _SOURCES)
def test_editing_source_text_drops_index_rows_for_recompute(
    conn: sqlite3.Connection, _source: str, insert: str, edit: str, vectors: str, key: str, fts: str
) -> None:
    """删观众替换昵称、台词被标成没播出时，旧正文的词元与向量都删掉，等索引任务按新正文重算。"""
    v29_migrate(conn)
    source_id = int(conn.execute(insert).lastrowid)
    _index_row(conn, vectors, key, fts, source_id)

    conn.execute(edit, (source_id,))

    assert _indexed(conn, vectors, key, fts, source_id) == (0, 0)


def test_unrelated_column_updates_keep_index_rows(conn: sqlite3.Connection) -> None:
    """只改状态这类不进索引的列（承诺了结）不触发重算。"""
    v29_migrate(conn)
    source_id = int(conn.execute(_SOURCES[1][1]).lastrowid)
    _index_row(conn, "memory_vectors_commitment", "commitment_id", "memory_fts_commitment", source_id)

    conn.execute("UPDATE commitments SET status = 'closed' WHERE id = ?", (source_id,))

    assert _indexed(conn, "memory_vectors_commitment", "commitment_id", "memory_fts_commitment", source_id) == (1, 1)


def test_full_text_tables_have_secure_delete_on(conn: sqlite3.Connection) -> None:
    v29_migrate(conn)
    for *_rest, fts in _SOURCES:
        value = conn.execute(f"SELECT v FROM {fts}_config WHERE k = 'secure-delete'").fetchone()  # noqa: S608
        assert value is not None and int(value[0]) == 1, f"{fts} 删掉的词元会留在影子表里"


def test_cue_log_rows_follow_their_session_and_archive_line(conn: sqlite3.Connection) -> None:
    v29_migrate(conn)
    conn.execute("INSERT INTO live_sessions(id, started_at_ms) VALUES (2, 2000)")
    line_id = int(conn.execute(_SOURCES[0][1]).lastrowid)
    conn.execute(
        "INSERT INTO memory_cue_log(live_session_id, pushed_at_ms, card_kind, archive_line_id) VALUES (1, 5, '档案', ?)",
        (line_id,),
    )
    conn.execute("INSERT INTO memory_cue_log(live_session_id, pushed_at_ms, card_kind) VALUES (2, 6, '老梗')")

    conn.execute("DELETE FROM archive_lines WHERE id = ?", (line_id,))
    conn.execute("DELETE FROM live_sessions WHERE id = 2")

    assert conn.execute("SELECT COUNT(*) FROM memory_cue_log").fetchone()[0] == 0


def test_migrate_is_idempotent(conn: sqlite3.Connection) -> None:
    v29_migrate(conn)
    v29_migrate(conn)

    triggers = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert len(triggers) == 8
    assert len([name for name in _columns(conn, "archive_lines") if name == "keywords"]) == 1


async def test_fresh_database_gets_index_tables_and_secure_delete(tmp_path: Path) -> None:
    """全新库先跑建表语句、再从 1 跑到最新迁移：派生表、触发器与 secure-delete 都要在。"""
    store = SQLiteDatabase(tmp_path / "fresh.db")
    await store.initialize()
    await store.close()

    connection = sqlite3.connect(tmp_path / "fresh.db")
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"memory_cue_log", "memory_vectors_speech", "memory_fts_speech"} <= tables
        secure = connection.execute("SELECT v FROM memory_fts_speech_config WHERE k = 'secure-delete'").fetchone()
        assert secure is not None and int(secure[0]) == 1
        triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        assert "trg_memory_index_speech_edited" in triggers
    finally:
        connection.close()
