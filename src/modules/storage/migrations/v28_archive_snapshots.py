"""v27 → v28：滚动档案改成"快照引用原行"，并让升级后的第一次整理在前几场的档案上滚起来。

v27 让整理把上一份档案里仍然成立的行复制进本场名下（``from_session_id`` 记出处）。复制出来的
副本躲开了按场次过滤的删除路径（删观众只替换 ta 出现过的场次），也给层层转写留了口子。这一版
改为每个整理过的正式场留一份快照，快照只引用原来的档案行，不复制：

- ``archive_snapshots``：哪些场次写过快照（整理时连同快照一起写；一场的快照可以是空的，
  "写过空快照"与"滚动之前的旧场次"靠这张表分开）；
- ``archive_snapshot_lines``：快照里的每一行引用一条 ``archive_lines``，另记位置、是否长期、
  最近一次被写下或被再提起的那一场的开播时刻。原行被删（运营者删单行、按句删台词、改彩排、
  删场次）时外键级联撤掉所有快照里对它的引用。

迁移做三件事：

1. 建两张表（新库已由建表语句建好，这里幂等）；
2. 把 v27 整理出的副本换成对原行的引用，删掉副本，再删掉 v27 加的两列；
3. 冷启动：还没有任何快照、而最近一个已结束的正式场已经按旧逻辑整理过时，把它放回待整理队列。
   启动补整理随即在它之前几场的档案行上重新整理它（基底取法见档案仓储），升级前那几场的名字、
   住处不必等下一场直播才接上。最近那一场还没整理时什么都不改，它的第一次整理就是这样滚的。
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from src.modules.storage.migrations._common import column_exists, table_exists

_SNAPSHOT_SQL = (
    """
CREATE TABLE IF NOT EXISTS archive_snapshots (
    live_session_id INTEGER PRIMARY KEY REFERENCES live_sessions(id) ON DELETE CASCADE,
    written_at_ms   INTEGER NOT NULL
)
""",
    """
CREATE TABLE IF NOT EXISTS archive_snapshot_lines (
    live_session_id INTEGER NOT NULL REFERENCES archive_snapshots(live_session_id) ON DELETE CASCADE,
    archive_line_id INTEGER NOT NULL REFERENCES archive_lines(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    lasting         INTEGER NOT NULL DEFAULT 0,
    seen_at_ms      INTEGER NOT NULL,
    PRIMARY KEY (live_session_id, archive_line_id)
)
""",
    "CREATE INDEX IF NOT EXISTS idx_archive_snapshot_lines_line ON archive_snapshot_lines(archive_line_id)",
)


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：建快照表 → 副本换成引用 → 删 v27 两列 → 必要时把最近一场放回待整理。"""
    if not table_exists(conn, "archive_lines") or not table_exists(conn, "live_sessions"):
        return
    for statement in _SNAPSHOT_SQL:
        conn.execute(statement)
    if column_exists(conn, "archive_lines", "from_session_id"):
        _convert_copies(conn)
        conn.execute("DROP INDEX IF EXISTS idx_archive_lines_from_session")
        conn.execute("ALTER TABLE archive_lines DROP COLUMN from_session_id")
    if column_exists(conn, "archive_lines", "lasting"):
        conn.execute("ALTER TABLE archive_lines DROP COLUMN lasting")
    _requeue_latest_for_cold_start(conn)


def _started_at(conn: sqlite3.Connection, live_session_id: int) -> int:
    """场次的开播时刻；场次行不在了按 0 算（排在最久没被提起的位置）。"""
    row = conn.execute("SELECT started_at_ms FROM live_sessions WHERE id=?", (int(live_session_id),)).fetchone()
    return int(row[0] or 0) if row else 0


def _original_of(conn: sqlite3.Connection, *, from_session_id: int, evidence_chat_id: int) -> Optional[int]:
    """副本对应的原行：出处那一场里证据台词相同的那一行；原行已删返回 None。"""
    row = conn.execute(
        "SELECT id FROM archive_lines WHERE live_session_id=? AND evidence_chat_id=?"
        " AND (from_session_id IS NULL OR from_session_id=live_session_id) ORDER BY id LIMIT 1",
        (int(from_session_id), int(evidence_chat_id)),
    ).fetchone()
    return int(row[0]) if row else None


def _convert_copies(conn: sqlite3.Connection) -> None:
    """v27 整理过的每一场：本场自己的行与副本对应的原行按原顺序进这一场的快照，然后删副本。

    原行已经被删的副本直接丢掉——原行删了，它说的事本来就不该再出现。
    """
    owners = conn.execute(
        "SELECT DISTINCT a.live_session_id, s.settled_at_ms FROM archive_lines a"
        " JOIN live_sessions s ON s.id = a.live_session_id WHERE a.from_session_id IS NOT NULL"
    ).fetchall()
    for owner_id, settled_at_ms in owners:
        conn.execute(
            "INSERT OR IGNORE INTO archive_snapshots(live_session_id, written_at_ms) VALUES (?, ?)",
            (int(owner_id), int(settled_at_ms or 0)),
        )
        rows = conn.execute(
            "SELECT id, evidence_chat_id, from_session_id, lasting FROM archive_lines"
            " WHERE live_session_id=? ORDER BY id",
            (int(owner_id),),
        ).fetchall()
        position = 0
        for line_id, evidence_chat_id, from_session_id, lasting in rows:
            origin = int(from_session_id) if from_session_id is not None else int(owner_id)
            target: Optional[int] = int(line_id)
            if origin != int(owner_id):
                target = _original_of(conn, from_session_id=origin, evidence_chat_id=int(evidence_chat_id))
            if target is None:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO archive_snapshot_lines(live_session_id, archive_line_id, position,"
                " lasting, seen_at_ms) VALUES (?, ?, ?, ?, ?)",
                (int(owner_id), target, position, 1 if lasting else 0, _started_at(conn, origin)),
            )
            position += 1
    conn.execute("DELETE FROM archive_lines WHERE from_session_id IS NOT NULL AND from_session_id != live_session_id")


def _requeue_latest_for_cold_start(conn: sqlite3.Connection) -> None:
    """冷启动：一份快照都还没有、最近一个已结束的正式场已按旧逻辑整理过，就把它放回待整理。

    只认真整理过的场次（整理完成时刻不等于结账时刻；v24 给存量场次补写的完成时刻等于结账
    时刻，那些场次从没真正整理过）。库里一行正式场档案都没有时没有可接的事，不重跑。
    """
    if conn.execute("SELECT 1 FROM archive_snapshots LIMIT 1").fetchone() is not None:
        return
    has_archive = conn.execute(
        "SELECT 1 FROM archive_lines a JOIN live_sessions s ON s.id = a.live_session_id WHERE s.kind='live' LIMIT 1"
    ).fetchone()
    if has_archive is None:
        return
    latest = conn.execute(
        "SELECT id, ended_at_ms, settled_at_ms FROM live_sessions"
        " WHERE kind='live' AND ended_at_ms IS NOT NULL ORDER BY started_at_ms DESC, id DESC LIMIT 1"
    ).fetchone()
    if latest is None or latest[2] is None or int(latest[2]) == int(latest[1]):
        return
    conn.execute(
        "UPDATE live_sessions SET settled_at_ms=NULL, settle_attempts=0, settle_report=NULL WHERE id=?",
        (int(latest[0]),),
    )


__all__ = ["migrate"]
