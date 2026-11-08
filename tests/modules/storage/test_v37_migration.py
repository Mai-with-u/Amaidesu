"""v36 → v37 迁移：建 clip_segments 表与场次索引；重复执行安全；场次删行级联。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.migrations.v37_clip_segments import migrate as v37_migrate


def _to_v36(path: Path) -> None:
    """版本记录退回 36 并补一场场次行（clip_segments 的外键目标）。"""
    raw = sqlite3.connect(path)
    try:
        raw.execute("DELETE FROM schema_migrations WHERE version > 36")
        raw.execute("INSERT INTO live_sessions(started_at_ms, kind, updated_at_ms) VALUES (1000, 'live', 1000)")
        raw.commit()
    finally:
        raw.close()


def test_v37_creates_table_and_index(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    db = sqlite3.connect(db_path)
    try:
        from src.modules.storage.schema import build_schema_sql

        # 只建到 v36 的形状：先跑全量建表再删掉 v37 的表与版本记录
        db.executescript(build_schema_sql())
        db.execute("DROP TABLE clip_segments")
        db.execute("DROP INDEX IF EXISTS idx_clip_segments_session")
        db.execute("DELETE FROM schema_migrations WHERE version > 36")
        db.commit()
        _to_v36(db_path)
        v37_migrate(db)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "clip_segments" in tables
        indexes = {row[1] for row in db.execute("PRAGMA index_list('clip_segments')")}
        assert "idx_clip_segments_session" in indexes
    finally:
        db.close()


def test_v37_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    db = sqlite3.connect(db_path)
    try:
        from src.modules.storage.schema import build_schema_sql

        db.executescript(build_schema_sql())
        _to_v36(db_path)
        v37_migrate(db)
        v37_migrate(db)  # 重复执行不抛、不多建
        count = db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='clip_segments'").fetchone()[
            0
        ]
        assert count == 1
    finally:
        db.close()


async def test_clip_segments_schema_version_and_cascade(store) -> None:
    """新库直建最新形状；场次删行时切片段级联删。"""
    from src.modules.storage.models.clipping import NewClipSegment
    from src.modules.storage.schema import SCHEMA_VERSION

    session_id = await store.sessions.insert_live_session(started_at_ms=1000)
    segment = await store.clips.add_segment(
        segment=NewClipSegment(live_session_id=session_id, start_ms=1100, end_ms=2000, title="一段")
    )
    assert segment.id > 0
    await store.sessions.delete_live_session(live_session_id=session_id)
    assert await store.clips.get_segment(segment_id=segment.id) is None
    assert SCHEMA_VERSION >= 37
