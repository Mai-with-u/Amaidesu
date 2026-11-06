"""v31 → v32 迁移：游戏里的人五张表、承诺与台词的玩家引用列，以及删除时的级联口径。

玩家表一行被删时，他的互动、公屏聊天、待审自述、观众关联、对他的承诺随外键一起删；她回应他的台词
留着，只把"在回应哪位玩家"置空。场次被删时这一场的互动、公屏聊天、待审自述随之删，玩家留着。
删观众只撤掉关联这一行（连带删玩家由隐私服务按关联做，不靠外键）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import AsyncIterator

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v32_game_players import migrate as v32_migrate
from src.modules.storage.schema import SCHEMA_VERSION

# v31 时的承诺表与台词表：没有玩家引用列（直播机上已经开过几场的库就是这个形状）
_V31_COMMITMENTS = """
CREATE TABLE commitments (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    what               TEXT NOT NULL,
    quote              TEXT NOT NULL DEFAULT '',
    evidence_chat_id   INTEGER UNIQUE,
    viewer_id          INTEGER,
    game               TEXT,
    save_id            TEXT NOT NULL DEFAULT '',
    save_name          TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'void')),
    created_session_id INTEGER,
    close_chat_id      INTEGER,
    close_quote        TEXT,
    created_at_ms      INTEGER NOT NULL,
    updated_at_ms      INTEGER NOT NULL,
    keywords           TEXT NOT NULL DEFAULT '[]'
);
"""
_V31_LIVE_CHAT = """
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
    simulated        INTEGER NOT NULL DEFAULT 0,
    utterance_id     TEXT,
    dropped_at_ms    INTEGER,
    memory_refs      TEXT
);
"""
_NEW_TABLES = ("player_candidates", "viewer_player_links", "game_chat", "game_player_interactions", "game_players")


async def test_v31_database_upgrades_with_player_columns_and_tables(tmp_path: Path) -> None:
    """承诺表与台词表早就存在的旧库：先执行建表汇总、后跑迁移，两列与五张表都补上、旧数据不动。"""
    path = tmp_path / "data" / "v31.db"
    fresh = SQLiteDatabase(path)
    await fresh.initialize()
    await fresh.close()
    # 把两张既有表换回 v31 的形状、删掉新表、版本记录退回 31，模拟直播机上的库
    raw = sqlite3.connect(path)
    try:
        drops = "".join(f"DROP TABLE {table};" for table in _NEW_TABLES)
        raw.executescript(
            drops
            + "DROP TABLE commitments; DROP TABLE live_chat;"
            + _V31_COMMITMENTS
            + _V31_LIVE_CHAT
            + "DELETE FROM schema_migrations WHERE version > 31;"
        )
        raw.execute("INSERT INTO commitments(what, created_at_ms, updated_at_ms) VALUES ('下次带大家挖钻石', 1, 1)")
        raw.execute(
            "INSERT INTO live_chat(live_session_id, timestamp_ms, sender_role, content, message_type)"
            " VALUES (1, 5, 'assistant', '好呀', 'text')"
        )
        raw.commit()
    finally:
        raw.close()

    upgraded = SQLiteDatabase(path)
    await upgraded.initialize()
    try:
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
        (commitment,) = await upgraded.execute("SELECT what, target_player_id, evidence_game_chat_id FROM commitments")
        assert (commitment["what"], commitment["target_player_id"], commitment["evidence_game_chat_id"]) == (
            "下次带大家挖钻石",
            None,
            None,
        )
        (line,) = await upgraded.execute("SELECT content, reply_player_id FROM live_chat")
        assert (line["content"], line["reply_player_id"]) == ("好呀", None)
        for table in _NEW_TABLES:
            assert await upgraded.table_exists(table)
        commitment_indexes = {row["name"] for row in await upgraded.execute("PRAGMA index_list(commitments)")}
        chat_indexes = {row["name"] for row in await upgraded.execute("PRAGMA index_list(live_chat)")}
        assert "idx_commitments_target_player" in commitment_indexes
        assert "idx_commitments_evidence_game_chat" in commitment_indexes
        assert "idx_live_chat_reply_player" in chat_indexes
    finally:
        await upgraded.close()


@pytest.fixture()
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    db = SQLiteDatabase(tmp_path / "data" / "v32.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


def _seed(conn: sqlite3.Connection) -> dict[str, int]:
    """造一场：玩家 Alex 打了招呼、在公屏说了一句，她播出一句回应他并答应了一件事，另有一条待审自述。"""
    session = int(
        conn.execute(
            "INSERT INTO live_sessions(started_at_ms, kind, updated_at_ms) VALUES (1000, 'live', 1000)"
        ).lastrowid
        or 0
    )
    player = int(
        conn.execute(
            "INSERT INTO game_players(game, uuid, name, first_session_id, first_seen_ms, last_session_id,"
            " last_seen_ms) VALUES ('minecraft', 'uuid-alex', 'Alex', ?, 1100, ?, 1100)",
            (session, session),
        ).lastrowid
        or 0
    )
    conn.execute(
        "INSERT INTO game_player_interactions(player_id, live_session_id, kind, fact, at_ms)"
        " VALUES (?, ?, 'greet', '{\"count\": 2}', 1100)",
        (player, session),
    )
    said = int(
        conn.execute(
            "INSERT INTO game_chat(live_session_id, game, direction, player_id, content, at_ms)"
            " VALUES (?, 'minecraft', 'player', ?, '我是这服服主', 1200)",
            (session, player),
        ).lastrowid
        or 0
    )
    reply = int(
        conn.execute(
            "INSERT INTO live_chat(live_session_id, timestamp_ms, sender_role, content, message_type, reply_player_id)"
            " VALUES (?, 1300, 'assistant', '好的服主，下次帮你修房子', 'text', ?)",
            (session, player),
        ).lastrowid
        or 0
    )
    conn.execute(
        "INSERT INTO commitments(what, quote, evidence_chat_id, target_player_id, created_session_id, created_at_ms,"
        " updated_at_ms) VALUES ('帮 Alex 修房子', '下次帮你修房子', ?, ?, ?, 1300, 1300)",
        (reply, player, session),
    )
    conn.execute(
        "INSERT INTO player_candidates(live_session_id, player_id, title, player_chat_id, player_quote,"
        " reply_live_chat_id, reply_quote, created_at_ms) VALUES (?, ?, '他是这服服主', ?, '我是这服服主', ?,"
        " '好的服主', 1400)",
        (session, player, said, reply),
    )
    viewer = int(
        conn.execute(
            "INSERT INTO viewers(platform, account_id, user_name, last_active_ms) VALUES ('bilibili', 'UID_9', '阿杰', 1)"
        ).lastrowid
        or 0
    )
    conn.execute(
        "INSERT INTO viewer_player_links(viewer_id, player_id, confirmed_at_ms) VALUES (?, ?, 1500)", (viewer, player)
    )
    return {"session": session, "player": player, "reply": reply, "viewer": viewer}


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608 测试里的常量表名


async def test_deleting_a_player_takes_everything_about_him(store: SQLiteDatabase) -> None:
    with store.manager.transaction() as conn:
        ids = _seed(conn)
        conn.execute("DELETE FROM game_players WHERE id=?", (ids["player"],))
        for table in ("game_player_interactions", "game_chat", "player_candidates", "viewer_player_links"):
            assert _count(conn, table) == 0, table
        # 对他的承诺随他删；她回应他的那句台词留着，只是不再记得在回应谁
        assert _count(conn, "commitments") == 0
        row = conn.execute("SELECT reply_player_id FROM live_chat WHERE id=?", (ids["reply"],)).fetchone()
        assert row["reply_player_id"] is None
        assert _count(conn, "viewers") == 1


async def test_deleting_the_session_keeps_the_player(store: SQLiteDatabase) -> None:
    with store.manager.transaction() as conn:
        ids = _seed(conn)
    assert await store.sessions.delete_live_session(live_session_id=ids["session"]) is True
    with store.manager.transaction() as conn:
        for table in ("game_player_interactions", "game_chat", "player_candidates"):
            assert _count(conn, table) == 0, table
        player = conn.execute(
            "SELECT first_session_id, last_session_id FROM game_players WHERE id=?", (ids["player"],)
        ).fetchone()
        assert (player["first_session_id"], player["last_session_id"]) == (None, None)
        assert _count(conn, "viewer_player_links") == 1


async def test_deleting_the_viewer_only_drops_the_link(store: SQLiteDatabase) -> None:
    with store.manager.transaction() as conn:
        ids = _seed(conn)
        conn.execute("DELETE FROM viewers WHERE id=?", (ids["viewer"],))
        assert _count(conn, "viewer_player_links") == 0
        assert _count(conn, "game_players") == 1


async def test_game_chat_direction_is_a_closed_set(store: SQLiteDatabase) -> None:
    """公屏聊天只有玩家说的、她说的、认不出是谁三种；私聊这类方向写不进来。"""
    with store.manager.transaction() as conn:
        ids = _seed(conn)
    with store.manager.transaction() as conn, pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(
            "INSERT INTO game_chat(live_session_id, game, direction, content, at_ms)"
            " VALUES (?, 'minecraft', 'whisper', '悄悄话', 1)",
            (ids["session"],),
        )


def test_migrate_is_idempotent() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(
            "CREATE TABLE live_sessions (id INTEGER PRIMARY KEY);"
            "CREATE TABLE viewers (id INTEGER PRIMARY KEY);" + _V31_COMMITMENTS + _V31_LIVE_CHAT
        )
        v32_migrate(conn)
        v32_migrate(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(commitments)")}
        assert "target_player_id" in columns
    finally:
        conn.close()
