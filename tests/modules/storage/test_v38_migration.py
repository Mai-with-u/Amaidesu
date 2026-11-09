"""v37 → v38 迁移：建 game_social_facts 表与两个索引；重复执行安全；证据行删除后置空。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v38_game_social_facts import migrate as v38_migrate
from src.modules.storage.schema import SCHEMA_VERSION


def _to_v37(path: Path) -> None:
    """版本记录退回 37 并删掉新表（模拟还没升到 v38 的旧库）。"""
    raw = sqlite3.connect(path)
    try:
        raw.execute("DROP TABLE IF EXISTS game_social_facts")
        raw.execute("DELETE FROM schema_migrations WHERE version > 37")
        raw.commit()
    finally:
        raw.close()


def test_v38_creates_table_and_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    db = sqlite3.connect(db_path)
    try:
        from src.modules.storage.schema import build_schema_sql

        db.executescript(build_schema_sql())
        _to_v37(db_path)
        v38_migrate(db)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "game_social_facts" in tables
        indexes = {row[1] for row in db.execute("PRAGMA index_list('game_social_facts')")}
        assert "idx_game_social_facts_scope" in indexes
        assert "idx_game_social_facts_status" in indexes
    finally:
        db.close()


def test_v38_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    db = sqlite3.connect(db_path)
    try:
        from src.modules.storage.schema import build_schema_sql

        db.executescript(build_schema_sql())
        _to_v37(db_path)
        v38_migrate(db)
        v38_migrate(db)  # 重复执行不抛、不多建
        count = db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='game_social_facts'"
        ).fetchone()[0]
        assert count == 1
    finally:
        db.close()


async def test_old_db_upgrades_and_keeps_existing_data(tmp_path: Path) -> None:
    """旧库升级：版本推进到位、既有表数据不动、新表可用。"""
    path = tmp_path / "data" / "v38.db"
    fresh = SQLiteDatabase(path)
    await fresh.initialize()
    await fresh.close()
    _to_v37(path)

    upgraded = SQLiteDatabase(path)
    await upgraded.initialize()
    try:
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
        assert await upgraded.table_exists("game_social_facts")
        await upgraded.execute(
            "INSERT INTO live_sessions(started_at_ms, kind, updated_at_ms) VALUES (1000, 'live', 1000)"
        )
        await upgraded.execute(
            "INSERT INTO game_social_facts(game, kind, text, created_at_ms, updated_at_ms)"
            " VALUES ('minecraft', 'members', 'Alex 在这个存档里帮忙修房子', 1100, 1100)"
        )
        rows = await upgraded.execute("SELECT kind, text FROM game_social_facts")
        assert [(row["kind"], row["text"]) for row in rows] == [("members", "Alex 在这个存档里帮忙修房子")]
    finally:
        await upgraded.close()


async def test_evidence_row_deletion_nulls_reference(tmp_path: Path) -> None:
    """证据行被删时事实行保留，证据引用置空（事实不跟着证据消失）。"""
    db = SQLiteDatabase(tmp_path / "data" / "v38.db")
    await db.initialize()
    try:
        session_id = await db.sessions.insert_live_session(started_at_ms=1000)
        chat_id = await db.chat.insert_live_chat(
            live_session_id=session_id,
            timestamp_ms=1100,
            platform="bilibili",
            sender_role="viewer",
            content="我负责挖矿",
            message_type="danmaku",
        )
        fact_id = await db.social_facts.insert_fact(
            kind="members",
            text="他在这局负责挖矿",
            quote="我负责挖矿",
            game="minecraft",
            evidence_chat_id=chat_id,
        )
        assert fact_id is not None
        await db.execute("DELETE FROM live_chat WHERE id=?", (chat_id,))
        fact = await db.social_facts.get_fact(fact_id=fact_id)
        assert fact is not None
        assert fact.evidence_chat_id is None
        assert fact.quote == "我负责挖矿"
    finally:
        await db.close()


_V37_GAME_CHAT = """
CREATE TABLE game_chat (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    game            TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('player', 'streamer', 'other')),
    player_id       INTEGER REFERENCES game_players(id) ON DELETE CASCADE,
    content         TEXT NOT NULL DEFAULT '',
    save_id         TEXT NOT NULL DEFAULT '',
    save_name       TEXT NOT NULL DEFAULT '',
    at_ms           INTEGER NOT NULL
);
"""


async def test_old_game_chat_gains_message_id_column_and_index(tmp_path: Path) -> None:
    """v37 形状的公屏表（没有消息号列）：升级后补列与索引、旧行补空串，重复执行不再变。"""
    path = tmp_path / "data" / "v37.db"
    fresh = SQLiteDatabase(path)
    await fresh.initialize()
    await fresh.close()
    raw = sqlite3.connect(path)
    try:
        raw.executescript("PRAGMA foreign_keys=OFF; DROP TABLE game_chat;" + _V37_GAME_CHAT)
        raw.execute("INSERT INTO live_sessions(started_at_ms, kind, updated_at_ms) VALUES (1, 'live', 1)")
        raw.execute(
            "INSERT INTO game_chat(live_session_id, game, direction, content, at_ms) VALUES (1, 'mc', 'other', '', 5)"
        )
        raw.commit()
    finally:
        raw.close()
    _to_v37(path)

    upgraded = SQLiteDatabase(path)
    await upgraded.initialize()
    try:
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
        (row,) = await upgraded.execute("SELECT message_id FROM game_chat")
        assert row["message_id"] == ""
        indexes = {r["name"] for r in await upgraded.execute("PRAGMA index_list(game_chat)")}
        assert "idx_game_chat_message" in indexes
    finally:
        await upgraded.close()

    conn = sqlite3.connect(path)
    try:
        v38_migrate(conn)
        v38_migrate(conn)
        columns = [r[1] for r in conn.execute("PRAGMA table_info(game_chat)")]
        assert columns.count("message_id") == 1
    finally:
        conn.close()
