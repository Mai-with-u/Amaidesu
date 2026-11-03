"""v23 迁移测试：live_chat 送达列与唯一索引、viewers 来访计数回填、forget_list 表与 secure_delete。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v23_privacy_and_speech_delivery import migrate as v23_migrate
from src.modules.storage.schema import SCHEMA_VERSION, list_expected_tables


def _make_v22_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE live_sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            stream_id       TEXT NOT NULL DEFAULT '',
            platform        TEXT NOT NULL DEFAULT 'unknown',
            started_at_ms   INTEGER NOT NULL,
            ended_at_ms     INTEGER,
            title           TEXT,
            source          TEXT NOT NULL DEFAULT 'manual',
            kind            TEXT NOT NULL DEFAULT 'live' CHECK (kind IN ('live', 'rehearsal')),
            heat            INTEGER NOT NULL DEFAULT 0,
            viewer_count    INTEGER NOT NULL DEFAULT 0,
            audience_total  INTEGER NOT NULL DEFAULT 0,
            updated_at_ms   INTEGER NOT NULL,
            notebook_text   TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE live_chat (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            live_session_id  INTEGER NOT NULL,
            timestamp_ms     INTEGER NOT NULL,
            platform         TEXT NOT NULL DEFAULT '',
            sender_role      TEXT NOT NULL,
            sender_id        TEXT,
            sender_name      TEXT,
            viewer_id        INTEGER,
            id_kind          TEXT NOT NULL DEFAULT '',
            content          TEXT NOT NULL,
            message_type     TEXT NOT NULL,
            message_id       TEXT,
            reply_to_message_id TEXT,
            simulated        INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE viewers (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            platform            TEXT NOT NULL DEFAULT '',
            account_id          TEXT,
            open_id             TEXT,
            open_app_id         TEXT,
            user_name           TEXT NOT NULL,
            name_seen_ms        INTEGER NOT NULL DEFAULT 0,
            simulated           INTEGER NOT NULL DEFAULT 0,
            link_status         TEXT NOT NULL DEFAULT 'confirmed',
            link_candidate_id   INTEGER,
            message_count       INTEGER NOT NULL DEFAULT 0,
            gift_count          INTEGER NOT NULL DEFAULT 0,
            replied_count       INTEGER NOT NULL DEFAULT 0,
            interaction_count   INTEGER NOT NULL DEFAULT 0,
            paid_count          INTEGER NOT NULL DEFAULT 0,
            paid_amount         INTEGER NOT NULL DEFAULT 0,
            last_active_ms      INTEGER NOT NULL
        );
        """
    )


def test_v23_callback_backfills_formal_sessions_and_is_idempotent() -> None:
    """只统计真实观众在正式场（kind='live'）的非模拟弹幕场次数，彩排场、模拟弹幕、上舰行都不计；重复执行幂等。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _make_v22_schema(conn)

    # 场次：1 正式、2 彩排、3 正式
    conn.execute("INSERT INTO live_sessions(id, started_at_ms, kind, updated_at_ms) VALUES (1, 1000, 'live', 1000)")
    conn.execute(
        "INSERT INTO live_sessions(id, started_at_ms, kind, updated_at_ms) VALUES (2, 2000, 'rehearsal', 2000)"
    )
    conn.execute("INSERT INTO live_sessions(id, started_at_ms, kind, updated_at_ms) VALUES (3, 3000, 'live', 3000)")

    # 观众：10 号阿杰（正式场 1、3 发弹幕，彩排场 2 也发过），20 号路人（只送礼没发过正式场弹幕），30 号模拟观众
    conn.execute(
        "INSERT INTO viewers(id, platform, account_id, user_name, simulated, last_active_ms)"
        " VALUES (10, 'bilibili', 'u10', '阿杰', 0, 3500)"
    )
    conn.execute(
        "INSERT INTO viewers(id, platform, account_id, user_name, simulated, last_active_ms)"
        " VALUES (20, 'bilibili', 'u20', '路人', 0, 2500)"
    )
    conn.execute(
        "INSERT INTO viewers(id, platform, account_id, user_name, simulated, last_active_ms)"
        " VALUES (30, 'bilibili', 'u30', '假人', 1, 3500)"
    )

    # 阿杰在正式场 1 发了两条弹幕，在彩排场 2 发了一条，在正式场 3 发了一条真弹幕和一条回放模拟弹幕
    conn.executemany(
        "INSERT INTO live_chat(live_session_id, timestamp_ms, sender_role, viewer_id, content, message_type, simulated)"
        " VALUES (?, ?, 'viewer', ?, 'hi', ?, ?)",
        [
            (1, 1100, 10, "danmaku", 0),
            (1, 1200, 10, "danmaku", 0),
            (2, 2100, 10, "danmaku", 0),
            (3, 3100, 10, "danmaku", 0),
            (3, 3200, 10, "danmaku", 1),
            # 路人在正式场 1 只有 guard 行和彩排场弹幕，没有正式场真弹幕
            (1, 1300, 20, "guard", 0),
            (2, 2200, 20, "danmaku", 0),
            # 模拟观众
            (1, 1400, 30, "danmaku", 1),
        ],
    )

    for _ in range(2):
        v23_migrate(conn)

    rows = {
        r["id"]: dict(r)
        for r in conn.execute(
            "SELECT id, sessions_count, last_session_id, prev_seen_ms, forget_requested_at_ms FROM viewers"
        )
    }
    assert rows[10] == {
        "id": 10,
        "sessions_count": 2,
        "last_session_id": 3,
        "prev_seen_ms": None,
        "forget_requested_at_ms": None,
    }
    assert rows[20]["sessions_count"] == 0 and rows[20]["last_session_id"] is None
    assert rows[30]["sessions_count"] == 0 and rows[30]["last_session_id"] is None

    # utterance_id 非空唯一，多行 NULL 允许共存
    conn.execute(
        "INSERT INTO live_chat(live_session_id, timestamp_ms, sender_role, content, message_type, utterance_id)"
        " VALUES (1, 1500, 'assistant', '第一句', 'speak', 'utt_1')"
    )
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "forget_list" in tables
    conn.close()


async def test_fresh_db_enables_secure_delete_and_includes_forget_list(db_path: Path) -> None:
    assert SCHEMA_VERSION >= 23
    assert "forget_list" in list_expected_tables()
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        await db.assert_schema_ready()
        row = await db.execute_fetchone("PRAGMA secure_delete")
        assert row is not None and int(row[0]) == 1
    finally:
        await db.close()
