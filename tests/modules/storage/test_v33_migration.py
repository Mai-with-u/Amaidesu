"""v32 → v33 迁移：改动记录多一列候选编号，候选被删时它的改动记录文本清空。

批准、驳回候选时记下是哪一条；候选因删观众、"忘了我"等被删后，编号与动作、时刻留着，
改前改后的文本（候选标题，可能提到被忘掉的人）清空。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v33_memory_edit_candidate import migrate as v33_migrate
from src.modules.storage.schema import SCHEMA_VERSION

# v32 时的改动记录表：没有候选编号列（直播机上审过候选的库就是这个形状）
_V32_MEMORY_EDITS = """
CREATE TABLE memory_edits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    target          TEXT NOT NULL,
    archive_line_id INTEGER,
    action          TEXT NOT NULL,
    before_text     TEXT NOT NULL DEFAULT '',
    after_text      TEXT NOT NULL DEFAULT '',
    at_ms           INTEGER NOT NULL
);
"""


def _to_v32(path: Path) -> None:
    """把新库的改动记录表换回 v32 的形状、删掉 v33 的触发器、版本记录退回 32，留一行旧记录。"""
    raw = sqlite3.connect(path)
    try:
        raw.executescript(
            "DROP TRIGGER IF EXISTS trg_memory_edits_candidate_deleted;"
            "DROP TABLE memory_edits;" + _V32_MEMORY_EDITS + "DELETE FROM schema_migrations WHERE version > 32;"
            "INSERT INTO live_sessions(started_at_ms, kind, updated_at_ms) VALUES (1000, 'live', 1000);"
            "INSERT INTO memory_edits(live_session_id, target, action, before_text, after_text, at_ms)"
            " VALUES (1, 'candidate', 'reject', '给大家讲鬼故事', '', 5);"
        )
        raw.commit()
    finally:
        raw.close()


async def test_v32_database_gains_candidate_column_and_keeps_old_edits(tmp_path: Path) -> None:
    """旧库先执行建表汇总、后跑迁移：列补上、旧记录的编号为空、文本不动；再跑一遍不报错。"""
    path = tmp_path / "data" / "v32.db"
    fresh = SQLiteDatabase(path)
    await fresh.initialize()
    await fresh.close()
    _to_v32(path)

    upgraded = SQLiteDatabase(path)
    await upgraded.initialize()
    try:
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
        (row,) = await upgraded.execute("SELECT before_text, candidate_id FROM memory_edits")
        assert (row["before_text"], row["candidate_id"]) == ("给大家讲鬼故事", None)
        triggers = {r["name"] for r in await upgraded.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        assert "trg_memory_edits_candidate_deleted" in triggers
    finally:
        await upgraded.close()

    # 幂等：已经是 v33 形状的库再跑一遍迁移，不重复加列、不报错
    raw = sqlite3.connect(path)
    try:
        v33_migrate(raw)
        columns = [r[1] for r in raw.execute("PRAGMA table_info(memory_edits)").fetchall()]
        assert columns.count("candidate_id") == 1
    finally:
        raw.close()


async def test_deleting_a_candidate_clears_its_edit_texts_only(tmp_path: Path) -> None:
    """候选被删：它的改动记录文本清空、编号与动作留着；别的候选、档案行的改动记录不动。"""
    db = SQLiteDatabase(tmp_path / "data" / "v33.db")
    await db.initialize()
    try:
        with db.manager.transaction() as conn:
            session = int(
                conn.execute(
                    "INSERT INTO live_sessions(started_at_ms, kind, updated_at_ms) VALUES (1000, 'live', 1000)"
                ).lastrowid
                or 0
            )
            chat = int(
                conn.execute(
                    "INSERT INTO live_chat(live_session_id, timestamp_ms, sender_role, content, message_type)"
                    " VALUES (?, 1100, 'assistant', '欠阿杰一个鬼故事', 'text')",
                    (session,),
                ).lastrowid
                or 0
            )
            ids = []
            for title in ("给阿杰讲鬼故事", "给大家唱歌"):
                ids.append(
                    int(
                        conn.execute(
                            "INSERT INTO viewer_candidates(live_session_id, kind, title, reply_chat_id, reply_quote,"
                            " status, created_at_ms) VALUES (?, 'claimed_missing', ?, ?, '欠', 'rejected', 1)",
                            (session, title, chat),
                        ).lastrowid
                        or 0
                    )
                )
            for candidate_id, title in zip(ids, ("给阿杰讲鬼故事", "给大家唱歌"), strict=True):
                conn.execute(
                    "INSERT INTO memory_edits(live_session_id, target, action, before_text, at_ms, candidate_id)"
                    " VALUES (?, 'candidate', 'reject', ?, 2, ?)",
                    (session, title, candidate_id),
                )
            conn.execute("DELETE FROM viewer_candidates WHERE id = ?", (ids[0],))
            rows = conn.execute("SELECT action, before_text, candidate_id FROM memory_edits ORDER BY id").fetchall()
        assert [(r[0], r[1], r[2]) for r in rows] == [("reject", "", ids[0]), ("reject", "给大家唱歌", ids[1])]
    finally:
        await db.close()
