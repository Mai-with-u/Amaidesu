"""v42 → v43 迁移：场次档案与滚动档案快照补名场面标记列 highlight、幂等升级与仓储读写验证。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v43_archive_highlights import migrate as v43_migrate
from src.modules.storage.models.memory import KeptSnapshotLine, PendingArchiveLine
from src.modules.storage.schema import SCHEMA_VERSION, build_schema_sql


def _to_v42(path: Path) -> None:
    """把新库回退到 v42 形态：重建不含 highlight 列的 archive_lines 与 archive_snapshot_lines 并塞入历史行。"""
    raw = sqlite3.connect(path)
    try:
        raw.execute("DROP TRIGGER IF EXISTS trg_memory_index_archive_deleted")
        raw.execute("DROP TRIGGER IF EXISTS trg_memory_index_archive_edited")
        raw.execute("DROP TRIGGER IF EXISTS trg_memory_edits_line_deleted")
        raw.execute("DROP TABLE IF EXISTS archive_snapshot_lines")
        raw.execute("DROP TABLE IF EXISTS archive_lines")
        raw.execute(
            """
            CREATE TABLE archive_lines (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                live_session_id  INTEGER NOT NULL,
                text             TEXT NOT NULL,
                quote            TEXT NOT NULL,
                evidence_chat_id INTEGER NOT NULL,
                game             TEXT,
                save_id          TEXT NOT NULL DEFAULT '',
                save_name        TEXT NOT NULL DEFAULT '',
                keywords         TEXT NOT NULL DEFAULT '[]',
                place            TEXT NOT NULL DEFAULT '',
                human_locked     INTEGER NOT NULL DEFAULT 0,
                source           TEXT NOT NULL DEFAULT 'streamer',
                viewer_chat_id   INTEGER REFERENCES live_chat(id) ON DELETE CASCADE,
                viewer_word      TEXT NOT NULL DEFAULT ''
            )
            """
        )
        raw.execute(
            """
            CREATE TABLE archive_snapshot_lines (
                live_session_id INTEGER NOT NULL REFERENCES archive_snapshots(live_session_id) ON DELETE CASCADE,
                archive_line_id INTEGER NOT NULL REFERENCES archive_lines(id) ON DELETE CASCADE,
                position        INTEGER NOT NULL,
                lasting         INTEGER NOT NULL DEFAULT 0,
                seen_at_ms      INTEGER NOT NULL,
                pinned          INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (live_session_id, archive_line_id)
            )
            """
        )
        raw.execute(
            "INSERT INTO live_sessions(id, stream_id, platform, started_at_ms, ended_at_ms, kind, updated_at_ms, settled_at_ms)"
            " VALUES (1, 'room', 'bilibili', 1000, 2000000, 'live', 2000000, 2001000)"
        )
        raw.execute(
            "INSERT INTO archive_lines(id, live_session_id, text, quote, evidence_chat_id, game, save_id, save_name)"
            " VALUES (10, 1, 'Minecraft：水车搭好了', '水车搭好啦', 3, 'minecraft', 's1', '一周目')"
        )
        raw.execute("INSERT INTO archive_snapshots(live_session_id, written_at_ms) VALUES (1, 2001000)")
        raw.execute(
            "INSERT INTO archive_snapshot_lines(live_session_id, archive_line_id, position, lasting, seen_at_ms, pinned)"
            " VALUES (1, 10, 0, 0, 1000, 0)"
        )
        raw.execute("DELETE FROM schema_migrations WHERE version > 42")
        raw.commit()
    finally:
        raw.close()


def test_v43_adds_highlight_columns_and_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "v43.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(build_schema_sql())
        conn.close()
        _to_v42(db_path)
        conn = sqlite3.connect(db_path)
        v43_migrate(conn)
        v43_migrate(conn)

        line_row = conn.execute("SELECT id, highlight FROM archive_lines WHERE id=10").fetchone()
        snap_row = conn.execute(
            "SELECT archive_line_id, highlight FROM archive_snapshot_lines WHERE live_session_id=1"
        ).fetchone()
        assert line_row == (10, 0)
        assert snap_row == (10, 0)
    finally:
        conn.close()


async def test_v42_db_upgrades_and_persists_highlight_across_settlement_and_pin(tmp_path: Path) -> None:
    path = tmp_path / "upgrade" / "amaidesu.db"
    fresh = SQLiteDatabase(path)
    await fresh.initialize()
    await fresh.close()
    _to_v42(path)

    store = SQLiteDatabase(path)
    await store.initialize()
    try:
        assert await store.get_schema_version() == SCHEMA_VERSION
        legacy_lines = await store.archive.list_lines(live_session_id=1)
        assert len(legacy_lines) == 1 and legacy_lines[0].highlight is False
        legacy_snap = await store.archive.latest_snapshot()
        assert legacy_snap is not None and legacy_snap.lines[0].highlight is False

        # 第二场整理写入一条名场面新事并接住旧行，第三场未接住后再由人工放回，名场面标记全程保留
        s2 = await store.sessions.insert_live_session(started_at_ms=5_000_000, kind="live")
        await store.sessions.close_live_session(live_session_id=s2, ended_at_ms=7_000_000)
        await store.sessions.apply_settlement(
            live_session_id=s2,
            archive_lines=[
                PendingArchiveLine(
                    text="Minecraft：跨海铁路通车翻车",
                    quote="第一趟车直接飞海里了",
                    evidence_chat_id=21,
                    game="minecraft",
                    save_id="s1",
                    save_name="一周目",
                    highlight=True,
                )
            ],
            commitments=[],
            closures=[],
            settled_at_ms=7_001_000,
            now_ms_value=7_001_000,
            snapshot_kept=[KeptSnapshotLine(archive_line_id=10, lasting=False, seen_at_ms=1000, highlight=False)],
        )
        s2_lines = await store.archive.list_lines(live_session_id=s2)
        assert len(s2_lines) == 1 and s2_lines[0].highlight is True
        hl_id = s2_lines[0].id
        snap2 = await store.archive.get_snapshot(live_session_id=s2)
        assert snap2 is not None
        assert [(item.line.id, item.highlight) for item in snap2.lines] == [(hl_id, True), (10, False)]

        s3 = await store.sessions.insert_live_session(started_at_ms=9_000_000, kind="live")
        await store.sessions.close_live_session(live_session_id=s3, ended_at_ms=11_000_000)
        await store.sessions.apply_settlement(
            live_session_id=s3,
            archive_lines=[],
            commitments=[],
            closures=[],
            settled_at_ms=11_001_000,
            now_ms_value=11_001_000,
            snapshot_kept=[KeptSnapshotLine(archive_line_id=10, lasting=False, seen_at_ms=1000)],
        )
        await store.memory_review.pin_line(line_id=hl_id, at_ms=11_002_000)
        snap3 = await store.archive.latest_snapshot()
        assert snap3 is not None
        assert [(item.line.id, item.pinned, item.highlight) for item in snap3.lines] == [
            (hl_id, True, True),
            (10, False, False),
        ]
    finally:
        await store.close()
