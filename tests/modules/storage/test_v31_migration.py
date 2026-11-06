"""v30 → v31 迁移：观众候选表与档案行的来源列，以及候选挂在台词记录上的删除口径。

候选和批准后的观众来源档案行都挂在两条台词记录上（观众那条弹幕、她那句回应），按外键级联：
删观众、忘了我、按句删弹幕或台词、删场次时随之删除；改彩排时台词还在，由场次仓储显式撤掉。
保留期清理只清工作数据：批准进记忆的那条弹幕不删，否则档案行会被级联删掉。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import AsyncIterator, Iterator, Tuple

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v31_viewer_candidates import migrate as v31_migrate
from src.modules.storage.privacy import PrivacyService
from src.modules.storage.retention import RetentionService
from src.modules.storage.schema import SCHEMA_VERSION

# v30 形状的最小 DDL：只留迁移和外键用得到的几张表
_V30_SCHEMA = """
CREATE TABLE live_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at_ms INTEGER NOT NULL);
CREATE TABLE live_chat (id INTEGER PRIMARY KEY AUTOINCREMENT, live_session_id INTEGER, content TEXT);
CREATE TABLE commitments (id INTEGER PRIMARY KEY AUTOINCREMENT, what TEXT NOT NULL);
CREATE TABLE archive_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    text             TEXT NOT NULL,
    quote            TEXT NOT NULL,
    evidence_chat_id INTEGER NOT NULL,
    human_locked     INTEGER NOT NULL DEFAULT 0
);
"""


@pytest.fixture()
def conn() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    # 生产连接统一打开外键；候选随台词记录删靠它
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_V30_SCHEMA)
    connection.execute("INSERT INTO live_sessions(id, started_at_ms) VALUES (1, 1000)")
    connection.execute(
        "INSERT INTO live_chat(id, live_session_id, content) VALUES (1, 1, '叫它团子吧'), (2, 1, '好，团子')"
    )
    connection.execute(
        "INSERT INTO archive_lines(id, live_session_id, text, quote, evidence_chat_id) VALUES (1, 1, '猫取名芝麻', '就叫芝麻', 2)"
    )
    try:
        yield connection
    finally:
        connection.close()


def _candidate(conn: sqlite3.Connection, *, viewer_chat_id: int | None, line_id: int | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO viewer_candidates(live_session_id, kind, title, viewer_chat_id, viewer_quote, reply_chat_id,"
        " reply_quote, created_at_ms, archive_line_id) VALUES (1, 'viewer_defined', '猫叫团子', ?, '叫它团子', 2,"
        " '团子', 5, ?)",
        (viewer_chat_id, line_id),
    )
    return int(cur.lastrowid or 0)


def test_old_lines_are_her_own_and_hang_on_no_viewer_message(conn: sqlite3.Connection) -> None:
    v31_migrate(conn)
    v31_migrate(conn)  # 幂等

    line = conn.execute("SELECT source, viewer_chat_id FROM archive_lines WHERE id = 1").fetchone()
    assert (line["source"], line["viewer_chat_id"]) == ("streamer", None)


def test_candidates_and_viewer_lines_follow_the_messages_they_hang_on(conn: sqlite3.Connection) -> None:
    """删那条观众弹幕：候选与批准出的档案行一起删；删她那句回应：候选删；档案行删了候选只是不再指向它。"""
    v31_migrate(conn)
    conn.execute(
        "INSERT INTO archive_lines(id, live_session_id, text, quote, evidence_chat_id, source, viewer_chat_id)"
        " VALUES (2, 1, '和观众一起给猫取名团子', '团子', 2, 'viewer', 1)"
    )
    approved = _candidate(conn, viewer_chat_id=1, line_id=2)
    conn.execute("INSERT INTO live_chat(id, live_session_id, content) VALUES (3, 1, '叫它年糕'), (4, 1, '年糕也行')")
    pending = conn.execute(
        "INSERT INTO viewer_candidates(live_session_id, kind, title, viewer_chat_id, reply_chat_id, reply_quote,"
        " created_at_ms) VALUES (1, 'viewer_defined', '狗叫年糕', 3, 4, '年糕也行', 5)"
    ).lastrowid
    claimed = conn.execute(
        "INSERT INTO viewer_candidates(live_session_id, kind, title, reply_chat_id, reply_quote, created_at_ms)"
        " VALUES (1, 'claimed_missing', '欠大家一个鬼故事', 4, '年糕也行', 5)"
    ).lastrowid

    conn.execute("DELETE FROM archive_lines WHERE id = 1")
    conn.execute("DELETE FROM live_chat WHERE id = 1")
    assert conn.execute("SELECT COUNT(*) FROM archive_lines WHERE id = 2").fetchone()[0] == 0
    assert conn.execute("SELECT id FROM viewer_candidates WHERE id = ?", (approved,)).fetchone() is None

    conn.execute("DELETE FROM live_chat WHERE id = 4")
    assert (
        conn.execute("SELECT COUNT(*) FROM viewer_candidates WHERE id IN (?, ?)", (pending, claimed)).fetchone()[0] == 0
    )


# v30 时的档案表：没有来源与观众弹幕两列（直播机上已经开过几场的库就是这个形状）
_V30_ARCHIVE_LINES = """
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
    human_locked     INTEGER NOT NULL DEFAULT 0
);
"""


async def test_v30_database_that_already_has_archive_lines_starts_after_upgrade(tmp_path: Path) -> None:
    """档案表早就存在的旧库：启动先执行建表汇总、后跑迁移，不能因为新列的索引在列补上之前就建而起不来。"""
    path = tmp_path / "data" / "v30.db"
    fresh = SQLiteDatabase(path)
    await fresh.initialize()
    await fresh.close()
    # 把档案表换回 v30 的形状、版本记录退回 30，模拟直播机上的库
    raw = sqlite3.connect(path)
    try:
        raw.executescript(
            "DROP TABLE viewer_candidates; DROP TABLE archive_lines;"
            + _V30_ARCHIVE_LINES
            + "DELETE FROM schema_migrations WHERE version > 30;"
        )
        raw.execute(
            "INSERT INTO archive_lines(live_session_id, text, quote, evidence_chat_id) VALUES (1, '猫取名芝麻', '就叫芝麻', 2)"
        )
        raw.commit()
    finally:
        raw.close()

    upgraded = SQLiteDatabase(path)
    await upgraded.initialize()
    try:
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
        rows = await upgraded.execute("SELECT text, source, viewer_chat_id FROM archive_lines")
        assert [(row["text"], row["source"], row["viewer_chat_id"]) for row in rows] == [
            ("猫取名芝麻", "streamer", None)
        ]
        indexes = {row["name"] for row in await upgraded.execute("PRAGMA index_list(archive_lines)")}
        assert "idx_archive_lines_viewer_chat" in indexes
    finally:
        await upgraded.close()


@pytest.fixture()
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    db = SQLiteDatabase(tmp_path / "data" / "v31.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


async def _viewer_line(store: SQLiteDatabase) -> Tuple[int, int, int, int]:
    """造一场：观众起名、她回应，人批准成一行观众来源的档案行，另有一条待审候选。

    返回 (场次, 观众编号, 档案行, 待审候选)。
    """
    session_id = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=2_000)
    viewer = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="UID_1",
        user_name="阿杰",
        timestamp_ms=1_100,
        name_match_window_ms=0,
    )
    said = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_100,
        sender_role="viewer",
        sender_id="UID_1",
        sender_name="阿杰",
        content="叫它团子吧",
        message_type="danmaku",
        viewer_id=viewer.viewer_id,
        id_kind="account",
    )
    reply = await store.chat.insert_live_chat(
        live_session_id=session_id, timestamp_ms=1_200, sender_role="assistant", content="好，团子", message_type="text"
    )
    with store.manager.transaction() as conn:
        line_id = int(
            conn.execute(
                "INSERT INTO archive_lines(live_session_id, text, quote, evidence_chat_id, source, viewer_chat_id,"
                " human_locked) VALUES (?, '和观众一起给猫取名团子', '团子', ?, 'viewer', ?, 1)",
                (session_id, reply, said),
            ).lastrowid
            or 0
        )
        pending = int(
            conn.execute(
                "INSERT INTO viewer_candidates(live_session_id, kind, title, viewer_chat_id, viewer_quote,"
                " reply_chat_id, reply_quote, created_at_ms) VALUES (?, 'viewer_defined', '狗叫年糕', ?, '叫它团子',"
                " ?, '好，团子', 1_500)",
                (session_id, said, reply),
            ).lastrowid
            or 0
        )
    return session_id, viewer.viewer_id, line_id, pending


def _count(store: SQLiteDatabase, table: str) -> int:
    with store.manager.transaction() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608 测试里的常量表名


async def test_fresh_database_reads_the_new_columns_back(store: SQLiteDatabase) -> None:
    session_id, _viewer, line_id, _pending = await _viewer_line(store)
    (line,) = await store.archive.list_lines(live_session_id=session_id)
    assert (line.id, line.source, line.viewer_chat_id is not None) == (line_id, "viewer", True)


async def test_forgetting_the_viewer_forgets_what_they_defined(store: SQLiteDatabase, tmp_path: Path) -> None:
    """删观众（忘了我最后也走这里）删 ta 的弹幕：ta 定下的设定与待审候选一起删。"""
    _session, viewer_id, _line, _pending = await _viewer_line(store)
    privacy = PrivacyService(store.manager, db_path=store.db_path, key_path=tmp_path / "secrets" / "forget.key")
    await privacy.initialize()
    try:
        assert await privacy.delete_viewer(viewer_id=viewer_id, add_to_forget_list=True)
    finally:
        await privacy.close()
    assert (_count(store, "archive_lines"), _count(store, "viewer_candidates")) == (0, 0)


async def test_rehearsal_drops_candidates_and_retention_keeps_approved_messages(
    store: SQLiteDatabase, tmp_path: Path
) -> None:
    session_id, _viewer, _line, _pending = await _viewer_line(store)
    privacy = PrivacyService(store.manager, db_path=store.db_path, key_path=tmp_path / "secrets" / "forget.key")
    await privacy.initialize()
    try:
        retention = RetentionService(store.manager, privacy, db_path=store.db_path, raw_days=1, clock=lambda: 10**12)
        await retention.run_cleanup()
    finally:
        await privacy.close()
    # 保留期只清工作数据：批准进记忆的那条弹幕留着，档案行跟着留着；待审候选挂的是同一条，也还在
    assert (_count(store, "archive_lines"), _count(store, "viewer_candidates")) == (1, 1)

    await store.sessions.set_live_session_kind_with_ledger(
        live_session_id=session_id, kind="rehearsal", updated_at_ms=3_000
    )
    assert (_count(store, "archive_lines"), _count(store, "viewer_candidates")) == (0, 0)
