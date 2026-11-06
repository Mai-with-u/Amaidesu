"""v33 → v34 迁移：观众候选与档案行各多一列"观众定下的名字或梗词"，旧行为空串，再跑一遍不报错。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v34_viewer_candidate_word import migrate as v34_migrate
from src.modules.storage.schema import SCHEMA_VERSION


def _to_v33(path: Path) -> None:
    """把新库的候选表与档案行表各去掉那一列、版本记录退回 33，留一条旧候选与一行旧档案。"""
    raw = sqlite3.connect(path)
    try:
        raw.executescript(
            "ALTER TABLE viewer_candidates DROP COLUMN word;"
            "ALTER TABLE archive_lines DROP COLUMN viewer_word;"
            "DELETE FROM schema_migrations WHERE version > 33;"
            "INSERT INTO live_sessions(started_at_ms, kind, updated_at_ms) VALUES (1000, 'live', 1000);"
            "INSERT INTO live_chat(live_session_id, timestamp_ms, sender_role, content, message_type)"
            " VALUES (1, 1100, 'assistant', '就叫团子', 'text');"
            "INSERT INTO viewer_candidates(live_session_id, kind, title, reply_chat_id, reply_quote, created_at_ms)"
            " VALUES (1, 'viewer_defined', '猫叫团子', 1, '就叫团子', 5);"
            "INSERT INTO archive_lines(live_session_id, text, quote, evidence_chat_id) VALUES (1, '猫叫团子', '团子', 1);"
        )
        raw.commit()
    finally:
        raw.close()


async def test_v33_database_gains_the_word_column_and_keeps_old_candidates(tmp_path: Path) -> None:
    """旧库升级：列补上、旧候选的词为空串（渲染时原话照旧全遮）、标题不动；再跑一遍迁移不报错。"""
    path = tmp_path / "data" / "v33.db"
    fresh = SQLiteDatabase(path)
    await fresh.initialize()
    await fresh.close()
    _to_v33(path)

    upgraded = SQLiteDatabase(path)
    await upgraded.initialize()
    try:
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
        (row,) = await upgraded.execute("SELECT title, word FROM viewer_candidates")
        assert (row["title"], row["word"]) == ("猫叫团子", "")
        (line,) = await upgraded.execute("SELECT text, viewer_word FROM archive_lines")
        assert (line["text"], line["viewer_word"]) == ("猫叫团子", "")
        with upgraded.manager.transaction() as conn:
            v34_migrate(conn)
    finally:
        await upgraded.close()
