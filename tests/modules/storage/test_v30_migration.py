"""v29 → v30 迁移：审核状态、人工锁定、放回快照标记与记忆页改动记录。

旧行一律未审、未锁、未放回；改动记录与档案同寿命（场次删了整场记录跟着删），行被删时记录留着、
文本清空。这里用 v29 形状的最小 DDL 造前置形状后直接调 ``migrate(conn)``，再用全新库走一遍启动路径，
最后经仓储确认新列能读写回来。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v30_memory_review import migrate as v30_migrate
from src.modules.storage.models.memory import KeptSnapshotLine, PendingArchiveLine

# v29 形状的最小 DDL：只留迁移和触发器用得到的四张表
_V29_SCHEMA = """
CREATE TABLE live_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_ms INTEGER NOT NULL,
    settle_report TEXT
);
CREATE TABLE archive_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    text             TEXT NOT NULL,
    quote            TEXT NOT NULL,
    evidence_chat_id INTEGER NOT NULL
);
CREATE TABLE archive_snapshots (
    live_session_id INTEGER PRIMARY KEY REFERENCES live_sessions(id) ON DELETE CASCADE,
    written_at_ms   INTEGER NOT NULL
);
CREATE TABLE archive_snapshot_lines (
    live_session_id INTEGER NOT NULL REFERENCES archive_snapshots(live_session_id) ON DELETE CASCADE,
    archive_line_id INTEGER NOT NULL REFERENCES archive_lines(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    lasting         INTEGER NOT NULL DEFAULT 0,
    seen_at_ms      INTEGER NOT NULL,
    PRIMARY KEY (live_session_id, archive_line_id)
);
"""


@pytest.fixture()
def conn() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    # 生产连接统一打开外键；改动记录跟着场次删靠它
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_V29_SCHEMA)
    connection.execute("INSERT INTO live_sessions(id, started_at_ms) VALUES (1, 1000), (2, 2000)")
    connection.execute(
        "INSERT INTO archive_lines(id, live_session_id, text, quote, evidence_chat_id) VALUES (1, 1, '猫取名芝麻', '就叫芝麻', 1)"
    )
    connection.execute("INSERT INTO archive_snapshots(live_session_id, written_at_ms) VALUES (1, 1500)")
    connection.execute(
        "INSERT INTO archive_snapshot_lines(live_session_id, archive_line_id, position, seen_at_ms) VALUES (1, 1, 0, 1000)"
    )
    try:
        yield connection
    finally:
        connection.close()


def _edit(conn: sqlite3.Connection, *, session_id: int, line_id: int, before: str, after: str) -> int:
    """记一条改标题的改动记录，返回记录行号。"""
    cur = conn.execute(
        "INSERT INTO memory_edits(live_session_id, target, archive_line_id, action, before_text, after_text, at_ms)"
        " VALUES (?, 'archive_line', ?, 'retitle', ?, ?, 5)",
        (session_id, line_id, before, after),
    )
    return int(cur.lastrowid)


def test_old_rows_start_unreviewed_unlocked_and_not_pinned(conn: sqlite3.Connection) -> None:
    v30_migrate(conn)

    session = conn.execute("SELECT reviewed_at_ms FROM live_sessions WHERE id = 1").fetchone()
    line = conn.execute("SELECT human_locked FROM archive_lines WHERE id = 1").fetchone()
    snapshot_line = conn.execute("SELECT pinned FROM archive_snapshot_lines").fetchone()
    assert session["reviewed_at_ms"] is None, "旧场次一律待审"
    assert line["human_locked"] == 0
    assert snapshot_line["pinned"] == 0


def test_deleting_a_line_keeps_its_edit_records_but_clears_their_text(conn: sqlite3.Connection) -> None:
    """删单行、按句删台词、改彩排都会删档案行：记录留着动作与时刻，被删的内容不留在记录里。"""
    v30_migrate(conn)
    conn.execute(
        "INSERT INTO archive_lines(id, live_session_id, text, quote, evidence_chat_id) VALUES (2, 1, '挖了水渠', '挖了水渠', 2)"
    )
    deleted = _edit(conn, session_id=1, line_id=1, before="猫取名芝麻", after="猫叫芝麻，住家里")
    kept = _edit(conn, session_id=1, line_id=2, before="挖了水渠", after="家门口挖了水渠")

    conn.execute("DELETE FROM archive_lines WHERE id = 1")

    rows = {row["id"]: row for row in conn.execute("SELECT id, action, before_text, after_text FROM memory_edits")}
    assert (rows[deleted]["action"], rows[deleted]["before_text"], rows[deleted]["after_text"]) == ("retitle", "", "")
    assert (rows[kept]["before_text"], rows[kept]["after_text"]) == ("挖了水渠", "家门口挖了水渠")


def test_edit_records_follow_their_session(conn: sqlite3.Connection) -> None:
    """与档案同寿命：场次被删，整场的改动记录一起删。"""
    v30_migrate(conn)
    _edit(conn, session_id=1, line_id=1, before="a", after="b")
    _edit(conn, session_id=2, line_id=1, before="c", after="d")

    conn.execute("DELETE FROM live_sessions WHERE id = 2")

    assert [row[0] for row in conn.execute("SELECT live_session_id FROM memory_edits")] == [1]


def test_migrate_is_idempotent(conn: sqlite3.Connection) -> None:
    v30_migrate(conn)
    v30_migrate(conn)

    triggers = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")]
    assert triggers == ["trg_memory_edits_line_deleted"]
    columns = [row[1] for row in conn.execute("PRAGMA table_info(archive_snapshot_lines)")]
    assert columns.count("pinned") == 1


async def test_fresh_database_reads_back_review_columns_through_the_repos(tmp_path: Path) -> None:
    """全新库走启动路径；仓储读出锁定、放回与审核状态，接进新快照时放回标记跟着带过去。"""
    store = SQLiteDatabase(tmp_path / "fresh.db")
    await store.initialize()
    try:
        first = await store.sessions.insert_live_session(started_at_ms=1000, kind="live")
        await store.sessions.close_live_session(live_session_id=first, ended_at_ms=2000)
        await store.sessions.apply_settlement(
            live_session_id=first,
            archive_lines=[PendingArchiveLine(text="给猫取名芝麻", quote="就叫芝麻", evidence_chat_id=1)],
            commitments=[],
            closures=[],
            settled_at_ms=2500,
            now_ms_value=2500,
        )
        (line,) = await store.archive.list_lines(live_session_id=first)
        assert line.human_locked is False
        # 模拟运营者在记忆页改过这一行、并标了已审：直接写列
        await store.execute("UPDATE archive_lines SET human_locked = 1 WHERE id = ?", (line.id,))
        await store.execute("UPDATE live_sessions SET reviewed_at_ms = 2600 WHERE id = ?", (first,))

        later = await store.sessions.insert_live_session(started_at_ms=3000, kind="live")
        await store.sessions.close_live_session(live_session_id=later, ended_at_ms=4000)
        await store.sessions.apply_settlement(
            live_session_id=later,
            archive_lines=[],
            commitments=[],
            closures=[],
            settled_at_ms=4500,
            now_ms_value=4500,
            snapshot_kept=[KeptSnapshotLine(archive_line_id=line.id, seen_at_ms=1000, pinned=True)],
        )

        snapshot = await store.archive.get_snapshot(live_session_id=later)
        assert snapshot is not None
        (item,) = snapshot.lines
        assert (item.pinned, item.line.human_locked) == (True, True)
        reviewed = await store.sessions.get_live_session(live_session_id=first)
        assert reviewed is not None and reviewed.reviewed_at_ms == 2600
        tables = {row["name"] for row in await store.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "memory_edits" in tables
    finally:
        await store.close()
