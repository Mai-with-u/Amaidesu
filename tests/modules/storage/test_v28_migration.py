"""v27 → v28 迁移：副本换成对原行的快照引用并删掉 v27 两列；冷启动把最近一场放回待整理。

用 v27 形状的最小 DDL 造前置形状后直接调用 v28 的 ``migrate(conn)``。
"""

from __future__ import annotations

import sqlite3
from typing import Iterator

import pytest

from src.modules.storage.migrations.v28_archive_snapshots import migrate as v28_migrate

# v27 形状的最小 DDL：场次表带整理三列，档案表带"出自哪一场""是否长期"两列
_V27_SCHEMA = """
CREATE TABLE live_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_ms   INTEGER NOT NULL,
    ended_at_ms     INTEGER,
    kind            TEXT NOT NULL DEFAULT 'live',
    settled_at_ms   INTEGER,
    settle_attempts INTEGER NOT NULL DEFAULT 0,
    settle_report   TEXT
);
CREATE TABLE archive_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    text             TEXT NOT NULL,
    quote            TEXT NOT NULL,
    evidence_chat_id INTEGER NOT NULL,
    game             TEXT,
    save_id          TEXT NOT NULL DEFAULT '',
    save_name        TEXT NOT NULL DEFAULT '',
    from_session_id  INTEGER,
    lasting          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_archive_lines_from_session ON archive_lines(from_session_id);
"""


@pytest.fixture()
def conn() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(_V27_SCHEMA)
    try:
        yield connection
    finally:
        connection.close()


def _session(conn: sqlite3.Connection, *, started: int, ended: int, settled: int | None, kind: str = "live") -> int:
    cur = conn.execute(
        "INSERT INTO live_sessions(started_at_ms, ended_at_ms, kind, settled_at_ms, settle_attempts, settle_report)"
        " VALUES (?, ?, ?, ?, 2, '{}')",
        (started, ended, kind, settled),
    )
    return int(cur.lastrowid)


def _line(conn: sqlite3.Connection, session_id: int, text: str, evidence: int, **extra: int) -> int:
    cur = conn.execute(
        "INSERT INTO archive_lines(live_session_id, text, quote, evidence_chat_id, from_session_id, lasting)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, text, text, evidence, extra.get("from_session_id"), extra.get("lasting", 0)),
    )
    return int(cur.lastrowid)


def _columns(conn: sqlite3.Connection) -> list[str]:
    return [row[1] for row in conn.execute("PRAGMA table_info(archive_lines)")]


def test_v27_copies_become_references_to_original_rows(conn: sqlite3.Connection) -> None:
    """v27 整理过的场次：自己的行与副本对应的原行按原顺序进快照（原行已删的副本丢掉），副本删除，两列删掉；
    已经有快照了，不再把最近一场放回待整理。"""
    first = _session(conn, started=1_000, ended=2_000, settled=2_500)
    original = _line(conn, first, "给狼取名", 1)
    second = _session(conn, started=3_000, ended=4_000, settled=4_500)
    own = _line(conn, second, "盖好木屋", 2, from_session_id=second)
    _line(conn, second, "给狼取名", 1, from_session_id=first, lasting=1)
    _line(conn, second, "原行已删的副本", 99, from_session_id=first)

    v28_migrate(conn)

    rows = conn.execute(
        "SELECT live_session_id, archive_line_id, position, lasting, seen_at_ms FROM archive_snapshot_lines"
        " ORDER BY live_session_id, position"
    ).fetchall()
    assert [tuple(row) for row in rows] == [(second, own, 0, 0, 3_000), (second, original, 1, 1, 1_000)]
    assert [row[0] for row in conn.execute("SELECT live_session_id FROM archive_snapshots")] == [second]
    assert [row[0] for row in conn.execute("SELECT id FROM archive_lines ORDER BY id")] == [original, own]
    assert "from_session_id" not in _columns(conn) and "lasting" not in _columns(conn)
    settled = conn.execute("SELECT settled_at_ms FROM live_sessions WHERE id=?", (second,)).fetchone()[0]
    assert settled == 4_500

    v28_migrate(conn)  # 幂等：列已删、表已建，再跑什么都不变
    assert conn.execute("SELECT COUNT(*) FROM archive_snapshot_lines").fetchone()[0] == 2


def test_cold_start_requeues_the_latest_session_settled_the_old_way(conn: sqlite3.Connection) -> None:
    """一份快照都没有、最近一场已按旧逻辑整理过：放回待整理（清整理时刻、次数与报告），更早的场次不动。"""
    earlier = _session(conn, started=1_000, ended=2_000, settled=2_500)
    _line(conn, earlier, "召出女仆取名", 1)
    latest = _session(conn, started=3_000, ended=4_000, settled=4_500)
    _line(conn, latest, "菜地围好了", 2)
    _session(conn, started=5_000, ended=6_000, settled=None, kind="rehearsal")

    v28_migrate(conn)

    row = conn.execute("SELECT settled_at_ms, settle_attempts, settle_report FROM live_sessions WHERE id=?", (latest,))
    assert tuple(row.fetchone()) == (None, 0, None)
    assert conn.execute("SELECT settled_at_ms FROM live_sessions WHERE id=?", (earlier,)).fetchone()[0] == 2_500


@pytest.mark.parametrize(
    ("latest_settled", "with_lines"),
    [(None, True), (4_000, True), (4_500, False)],
    ids=["latest-still-pending", "latest-marked-by-v24", "no-archive-at-all"],
)
def test_cold_start_leaves_sessions_alone_when_nothing_to_redo(
    conn: sqlite3.Connection, latest_settled: int | None, with_lines: bool
) -> None:
    """最近一场还没整理（它的第一次整理就会滚）、只是 v24 补写的完成时刻、或库里一行档案都没有：不改。"""
    earlier = _session(conn, started=1_000, ended=2_000, settled=2_500)
    if with_lines:
        _line(conn, earlier, "召出女仆取名", 1)
    latest = _session(conn, started=3_000, ended=4_000, settled=latest_settled)

    v28_migrate(conn)

    row = conn.execute("SELECT settled_at_ms, settle_attempts FROM live_sessions WHERE id=?", (latest,)).fetchone()
    assert tuple(row) == (latest_settled, 2)


def test_migrate_skips_missing_tables() -> None:
    """没有档案表或场次表的库什么都不做。"""
    bare = sqlite3.connect(":memory:")
    try:
        v28_migrate(bare)
        assert bare.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0
    finally:
        bare.close()
