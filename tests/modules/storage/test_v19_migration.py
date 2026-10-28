"""v18 → v19 迁移：``live_sessions`` 加场次类型列，存量场次按证据回填彩排。

先建最新库，再去掉 ``kind`` 列退回 v18 形状、写入存量场次与明细，重新打开触发迁移。
回填只认明确证据：回放来源，或"有观众明细却没有一条真实观众、也没收到过平台
观看数推送"；判不出真假的场次保持正式。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v19_live_session_kind import migrate as v19_migrate
from src.modules.storage.schema import SCHEMA_VERSION

# 场次编号 → (来源, 累计观众数, 回填后期望类型)
_SESSIONS: Dict[int, tuple] = {
    1: ("replay", 0, "rehearsal"),  # 模拟器回放：直接判彩排
    2: ("manual", 0, "rehearsal"),  # 只有模拟弹幕
    3: ("manual", 0, "rehearsal"),  # 只有控制台调试弹幕
    4: ("manual", 0, "live"),  # 模拟弹幕里混着一条真实 B 站弹幕
    5: ("manual", 0, "live"),  # 只有主播自己说话，判不出真假
    6: ("manual", 12, "live"),  # 只有模拟礼物，但收到过平台观看数推送
    7: ("manual", 0, "live"),  # 只有真实礼物，没有弹幕
    8: ("legacy", 0, "rehearsal"),  # 历史遗留行，只有控制台输入
}


def _chat(conn: sqlite3.Connection, session: int, *, platform: str, role: str = "viewer", simulated: int = 0) -> None:
    conn.execute(
        "INSERT INTO live_chat(live_session_id, timestamp_ms, platform, sender_role, content, message_type, simulated)"
        " VALUES (?, 1000, ?, ?, '弹幕', 'danmaku', ?)",
        (session, platform, role, simulated),
    )


def _gift(conn: sqlite3.Connection, session: int, *, simulated: int) -> None:
    conn.execute(
        "INSERT INTO gifts(live_session_id, timestamp_ms, platform, user_id, user_name, gift_name, quantity, simulated)"
        " VALUES (?, 1000, 'bilibili', '42', '观众', '小心心', 1, ?)",
        (session, simulated),
    )


def _make_v18(path: Path) -> None:
    """去掉 ``kind`` 列退回 v18 形状，并写入各类存量场次。"""
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE live_sessions DROP COLUMN kind")
    conn.execute("DELETE FROM schema_migrations WHERE version > 18")
    for pk, (source, audience_total, _) in _SESSIONS.items():
        conn.execute(
            "INSERT INTO live_sessions(id, started_at_ms, ended_at_ms, source, audience_total, updated_at_ms)"
            " VALUES (?, 1000, 2000, ?, ?, 2000)",
            (pk, source, audience_total),
        )
    _chat(conn, 1, platform="bilibili")
    _chat(conn, 2, platform="bilibili", simulated=1)
    _chat(conn, 3, platform="console")
    _chat(conn, 4, platform="bilibili", simulated=1)
    _chat(conn, 4, platform="bilibili")
    _chat(conn, 5, platform="", role="assistant")
    _gift(conn, 6, simulated=1)
    _gift(conn, 7, simulated=0)
    _chat(conn, 8, platform="console")
    conn.commit()
    conn.close()


def _kinds(path: Path) -> Dict[int, str]:
    conn = sqlite3.connect(path)
    try:
        return {int(r[0]): str(r[1]) for r in conn.execute("SELECT id, kind FROM live_sessions")}
    finally:
        conn.close()


async def test_new_database_has_kind_column(db_path: Path) -> None:
    """新库：DDL 已含 ``kind`` 列，新插入的场次默认正式。"""
    assert SCHEMA_VERSION >= 19
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        pk = await db.sessions.insert_live_session(started_at_ms=1)
        row = await db.sessions.get_live_session(live_session_id=pk)
        assert row is not None and row.kind == "live"
        rehearsal = await db.sessions.insert_live_session(started_at_ms=2, kind="rehearsal")
        rehearsal_row = await db.sessions.get_live_session(live_session_id=rehearsal)
        assert rehearsal_row is not None and rehearsal_row.kind == "rehearsal"
    finally:
        await db.close()


async def test_v18_database_backfills_rehearsal_only_on_evidence(db_path: Path) -> None:
    """v18 旧库升级：回放与"全是假观众"的场次判彩排，其余保持正式；再开一次零变更。"""
    db = SQLiteDatabase(db_path)
    await db.initialize()
    await db.close()
    _make_v18(db_path)

    upgraded = SQLiteDatabase(db_path)
    await upgraded.initialize()
    try:
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
    finally:
        await upgraded.close()
    expected = {pk: kind for pk, (_, _, kind) in _SESSIONS.items()}
    assert _kinds(db_path) == expected

    reopened = SQLiteDatabase(db_path)
    await reopened.initialize()
    await reopened.close()
    assert _kinds(db_path) == expected


async def test_rerun_keeps_manual_correction(db_path: Path) -> None:
    """回填只在补列那一次执行：列已存在时重跑回调，不覆盖人手更正过的类型。"""
    db = SQLiteDatabase(db_path)
    await db.initialize()
    await db.close()
    _make_v18(db_path)

    conn = sqlite3.connect(db_path)
    v19_migrate(conn)
    conn.execute("UPDATE live_sessions SET kind='live' WHERE id=2")
    v19_migrate(conn)
    conn.commit()
    conn.close()
    assert _kinds(db_path)[2] == "live"
