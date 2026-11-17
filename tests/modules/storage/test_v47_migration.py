"""v46 → v47 迁移：观众候选补 viewer_id 外键列、存量回填、索引、保留期保护与遗忘删除。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import AsyncIterator

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v47_viewer_self_candidates import migrate as v47_migrate
from src.modules.storage.models.memory import PendingArchiveLine, PendingViewerCandidate
from src.modules.storage.privacy import PrivacyService
from src.modules.storage.repos.viewer_candidates import VIEWER_SELF, replace_session_candidates
from src.modules.storage.retention import RetentionService
from src.modules.storage.schema import SCHEMA_VERSION, build_schema_sql

_DAY_MS = 86_400_000
_NOW_MS = 1_700_000_000_000


@pytest.fixture()
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    db = SQLiteDatabase(tmp_path / "v47_store.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


def _seed_v46_database(path: Path) -> None:
    """用去掉 viewer_candidates.viewer_id 列的 v46 DDL 建库，并写入存量候选行。"""
    v46_ddl = build_schema_sql().replace(
        "    word            TEXT NOT NULL DEFAULT '',\n    viewer_id       INTEGER REFERENCES viewers(id) ON DELETE CASCADE\n",
        "    word            TEXT NOT NULL DEFAULT ''\n",
    )
    assert (
        "viewer_id       INTEGER REFERENCES viewers(id)"
        not in v46_ddl.split("CREATE TABLE IF NOT EXISTS game_players")[0].split(
            "CREATE TABLE IF NOT EXISTS viewer_candidates"
        )[1]
    )
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(v46_ddl)
        for version in range(1, 47):
            conn.execute("INSERT INTO schema_migrations(version, applied_at_ms) VALUES (?, ?)", (version, 1_000))
        conn.execute(
            "INSERT INTO live_sessions(id, stream_id, platform, kind, started_at_ms, ended_at_ms, updated_at_ms)"
            " VALUES (1, 'room_1', 'bilibili', 'live', 1000, 2000, 2000)"
        )
        conn.execute(
            "INSERT INTO viewers(id, platform, account_id, user_name, last_active_ms)"
            " VALUES (7, 'bilibili', 'u_ajie', '阿杰', 1500)"
        )
        conn.execute(
            "INSERT INTO live_chat(id, live_session_id, timestamp_ms, sender_role, sender_name, viewer_id, content, message_type)"
            " VALUES (10, 1, 1100, 'viewer', '阿杰', 7, '叫它团子吧', 'danmaku')"
        )
        conn.execute(
            "INSERT INTO live_chat(id, live_session_id, timestamp_ms, sender_role, viewer_id, content, message_type)"
            " VALUES (11, 1, 1150, 'viewer', 999, '匿名路过', 'danmaku')"
        )
        conn.execute(
            "INSERT INTO live_chat(id, live_session_id, timestamp_ms, sender_role, content, message_type)"
            " VALUES (12, 1, 1200, 'assistant', '好，就叫团子', 'speech')"
        )
        conn.execute(
            "INSERT INTO live_chat(id, live_session_id, timestamp_ms, sender_role, content, message_type)"
            " VALUES (13, 1, 1300, 'assistant', '上次欠大家一个故事', 'speech')"
        )
        conn.execute(
            "INSERT INTO viewer_candidates(id, live_session_id, kind, title, viewer_chat_id, viewer_quote,"
            " reply_chat_id, reply_quote, status, created_at_ms, word)"
            " VALUES (1, 1, 'viewer_defined', '猫叫团子', 10, '叫它团子', 12, '就叫团子', 'pending', 1900, '团子')"
        )
        conn.execute(
            "INSERT INTO viewer_candidates(id, live_session_id, kind, title, viewer_chat_id, viewer_quote,"
            " reply_chat_id, reply_quote, status, created_at_ms, word)"
            " VALUES (2, 1, 'viewer_defined', '路过留言', 11, '匿名路过', 13, '欠大家一个故事', 'pending', 1900, '')"
        )
        conn.execute(
            "INSERT INTO viewer_candidates(id, live_session_id, kind, title, viewer_chat_id, viewer_quote,"
            " reply_chat_id, reply_quote, status, created_at_ms, word)"
            " VALUES (3, 1, 'claimed_missing', '讲故事', NULL, '', 13, '欠大家一个故事', 'pending', 1900, '')"
        )
        conn.commit()
    finally:
        conn.close()


def test_v47_migrate_callback_is_idempotent_on_empty_and_v46_tables(tmp_path: Path) -> None:
    empty_conn = sqlite3.connect(":memory:")
    try:
        v47_migrate(empty_conn)
        v47_migrate(empty_conn)
    finally:
        empty_conn.close()

    db_file = tmp_path / "callback_v46.db"
    _seed_v46_database(db_file)
    conn = sqlite3.connect(db_file)
    try:
        v47_migrate(conn)
        v47_migrate(conn)
        rows = conn.execute("SELECT id, viewer_id FROM viewer_candidates ORDER BY id").fetchall()
        assert rows == [(1, 7), (2, None), (3, None)]
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='viewer_candidates'"
            ).fetchall()
        }
        assert "idx_viewer_candidates_viewer" in indexes
    finally:
        conn.close()


async def test_v46_database_upgrades_to_v47_with_backup_backfill_and_index(tmp_path: Path) -> None:
    db_file = tmp_path / "upgrade_v46.db"
    _seed_v46_database(db_file)

    db = SQLiteDatabase(db_file)
    await db.initialize()
    try:
        assert SCHEMA_VERSION >= 47
        assert await db.get_schema_version() == SCHEMA_VERSION
        await db.assert_schema_ready()
        rows = await db.execute("SELECT id, viewer_id FROM viewer_candidates ORDER BY id")
        assert [(int(r["id"]), r["viewer_id"]) for r in rows] == [(1, 7), (2, None), (3, None)]
        indexes = await db.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='viewer_candidates'")
        assert "idx_viewer_candidates_viewer" in {str(r["name"]) for r in indexes}
    finally:
        await db.close()

    backups = list((tmp_path / "backups").glob(f"*-pre-v{SCHEMA_VERSION}-*.db"))
    assert len(backups) == 1

    # 二次打开已是当前版本：幂等无副作用，不再新增备份
    reopened = SQLiteDatabase(db_file)
    await reopened.initialize()
    try:
        assert await reopened.get_schema_version() == SCHEMA_VERSION
    finally:
        await reopened.close()
    assert len(list((tmp_path / "backups").glob("*-pre-v*.db"))) == 1


async def test_deleting_viewer_row_cascades_to_viewer_candidates(store: SQLiteDatabase) -> None:
    session_id = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=2_000)
    viewer = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_xiaoxia",
        user_name="小夏",
        timestamp_ms=1_050,
        name_match_window_ms=0,
    )
    said = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_100,
        sender_role="viewer",
        viewer_id=viewer.viewer_id,
        content="我平时喜欢拼像素画",
        message_type="danmaku",
    )
    reply = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_200,
        sender_role="assistant",
        content="像素画很考验耐心呀",
        message_type="speech",
    )
    with store.manager.transaction() as conn:
        replace_session_candidates(
            conn,
            live_session_id=session_id,
            candidates=[
                PendingViewerCandidate(
                    kind=VIEWER_SELF,
                    title="ta 平时喜欢拼像素画",
                    viewer_chat_id=said,
                    viewer_quote="平时喜欢拼像素画",
                    word="像素画",
                    reply_chat_id=reply,
                    reply_quote="像素画很考验耐心呀",
                    viewer_id=viewer.viewer_id,
                )
            ],
            created_at_ms=1_500,
        )
    (cand,) = await store.viewer_candidates.list_candidates(status=None)
    await store.viewer_candidates.approve(candidate_id=cand.id, at_ms=1_600)

    # 即使 viewer_chat_id 未先删除，直接删除 viewers 主行也经 ON DELETE CASCADE 连带清除候选
    await store.execute("DELETE FROM viewers WHERE id=?", (viewer.viewer_id,))
    assert await store.viewer_candidates.list_candidates(status=None) == []


async def test_retention_raw_days_preserves_approved_viewer_self_source_danmaku(
    store: SQLiteDatabase, tmp_path: Path
) -> None:
    session_id = await store.sessions.insert_live_session(started_at_ms=_NOW_MS - 20 * _DAY_MS, kind="live")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=_NOW_MS - 19 * _DAY_MS)
    viewer = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_laochen",
        user_name="老陈",
        timestamp_ms=_NOW_MS - 20 * _DAY_MS,
        name_match_window_ms=0,
    )

    chat_ids: list[int] = []
    reply_ids: list[int] = []
    for idx, text in enumerate(("我周末常去夜跑", "我最近在练口琴", "我养了一只玄凤鹦鹉", "晚上好呀")):
        chat_ids.append(
            await store.chat.insert_live_chat(
                live_session_id=session_id,
                timestamp_ms=_NOW_MS - 20 * _DAY_MS + idx * 100,
                sender_role="viewer",
                sender_name="老陈",
                viewer_id=viewer.viewer_id,
                content=text,
                message_type="danmaku",
            )
        )
        reply_ids.append(
            await store.chat.insert_live_chat(
                live_session_id=session_id,
                timestamp_ms=_NOW_MS - 20 * _DAY_MS + idx * 100 + 50,
                sender_role="assistant",
                content=f"回应第{idx}句",
                message_type="speech",
            )
        )

    await store.sessions.apply_settlement(
        live_session_id=session_id,
        archive_lines=[PendingArchiveLine(text="开播问候", quote="回应第3句", evidence_chat_id=reply_ids[3])],
        commitments=[],
        closures=[],
        settled_at_ms=_NOW_MS - 19 * _DAY_MS,
        now_ms_value=_NOW_MS - 19 * _DAY_MS,
    )
    with store.manager.transaction() as conn:
        replace_session_candidates(
            conn,
            live_session_id=session_id,
            candidates=[
                PendingViewerCandidate(
                    kind=VIEWER_SELF,
                    title="ta 周末常去夜跑",
                    viewer_chat_id=chat_ids[0],
                    viewer_quote="周末常去夜跑",
                    word="夜跑",
                    reply_chat_id=reply_ids[0],
                    reply_quote="回应第0句",
                    viewer_id=viewer.viewer_id,
                ),
                PendingViewerCandidate(
                    kind=VIEWER_SELF,
                    title="ta 最近在练口琴",
                    viewer_chat_id=chat_ids[1],
                    viewer_quote="最近在练口琴",
                    word="口琴",
                    reply_chat_id=reply_ids[1],
                    reply_quote="回应第1句",
                    viewer_id=viewer.viewer_id,
                ),
                PendingViewerCandidate(
                    kind=VIEWER_SELF,
                    title="ta 养了一只玄凤鹦鹉",
                    viewer_chat_id=chat_ids[2],
                    viewer_quote="玄凤鹦鹉",
                    word="玄凤鹦鹉",
                    reply_chat_id=reply_ids[2],
                    reply_quote="回应第2句",
                    viewer_id=viewer.viewer_id,
                ),
            ],
            created_at_ms=_NOW_MS - 19 * _DAY_MS,
        )

    by_title = {item.title: item for item in await store.viewer_candidates.list_candidates(status="pending")}
    await store.viewer_candidates.approve(candidate_id=by_title["ta 周末常去夜跑"].id, at_ms=_NOW_MS - 18 * _DAY_MS)
    await store.viewer_candidates.reject(candidate_id=by_title["ta 养了一只玄凤鹦鹉"].id, at_ms=_NOW_MS - 18 * _DAY_MS)

    privacy = PrivacyService(store.manager, db_path=store.db_path, key_path=tmp_path / "secrets" / "forget.key")
    await privacy.initialize()
    try:
        retention = RetentionService(
            store.manager,
            privacy,
            db_path=store.db_path,
            raw_days=7,
            clock=lambda: _NOW_MS,
        )
        result = await retention.run_cleanup(now_ms=_NOW_MS)
    finally:
        await privacy.close()

    # 4 条到期观众弹幕中：仅已批准近况挂着的第 0 条保留，其余 3 条（待审/已驳回/普通弹幕）照常清理
    assert result.chat_deleted == 3
    remaining_viewer_chats = await store.execute(
        "SELECT id, content FROM live_chat WHERE sender_role='viewer' ORDER BY id"
    )
    assert [(int(r["id"]), str(r["content"])) for r in remaining_viewer_chats] == [(chat_ids[0], "我周末常去夜跑")]
    notes = await store.viewers.approved_viewer_notes(viewer_ids=[viewer.viewer_id])
    assert [note.title for note in notes] == ["ta 周末常去夜跑"]


async def test_forget_me_deletes_viewer_self_notes_and_leaves_zero_self_text_in_edits(
    store: SQLiteDatabase, tmp_path: Path
) -> None:
    session_id = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=2_000)
    viewer = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_ajie",
        user_name="阿杰",
        timestamp_ms=1_050,
        name_match_window_ms=0,
    )
    chat_a = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_100,
        sender_role="viewer",
        sender_name="阿杰",
        viewer_id=viewer.viewer_id,
        content="我平时爱喝乌龙茶",
        message_type="danmaku",
    )
    reply_a = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_150,
        sender_role="assistant",
        content="乌龙茶很清爽",
        message_type="speech",
    )
    chat_b = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_200,
        sender_role="viewer",
        sender_name="阿杰",
        viewer_id=viewer.viewer_id,
        content="我周末会弹木吉他",
        message_type="danmaku",
    )
    reply_b = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_250,
        sender_role="assistant",
        content="木吉他音色很好听",
        message_type="speech",
    )
    with store.manager.transaction() as conn:
        replace_session_candidates(
            conn,
            live_session_id=session_id,
            candidates=[
                PendingViewerCandidate(
                    kind=VIEWER_SELF,
                    title="ta 平时爱喝乌龙茶",
                    viewer_chat_id=chat_a,
                    viewer_quote="爱喝乌龙茶",
                    word="乌龙茶",
                    reply_chat_id=reply_a,
                    reply_quote="乌龙茶很清爽",
                    viewer_id=viewer.viewer_id,
                ),
                PendingViewerCandidate(
                    kind=VIEWER_SELF,
                    title="ta 周末会弹木吉他",
                    viewer_chat_id=chat_b,
                    viewer_quote="弹木吉他",
                    word="木吉他",
                    reply_chat_id=reply_b,
                    reply_quote="木吉他音色很好听",
                    viewer_id=viewer.viewer_id,
                ),
            ],
            created_at_ms=1_500,
        )

    by_title = {item.title: item for item in await store.viewer_candidates.list_candidates(status="pending")}
    await store.viewer_candidates.approve(
        candidate_id=by_title["ta 平时爱喝乌龙茶"].id, title="ta 爱喝无糖乌龙茶", at_ms=1_600
    )
    await store.viewer_candidates.revoke(candidate_id=by_title["ta 平时爱喝乌龙茶"].id, at_ms=1_650)
    await store.viewer_candidates.approve(
        candidate_id=by_title["ta 平时爱喝乌龙茶"].id, title="ta 爱喝无糖乌龙茶", at_ms=1_700
    )
    await store.viewer_candidates.reject(candidate_id=by_title["ta 周末会弹木吉他"].id, at_ms=1_750)

    privacy = PrivacyService(store.manager, db_path=store.db_path, key_path=tmp_path / "secrets" / "forget.key")
    await privacy.initialize()
    try:
        assert await privacy.delete_viewer(viewer_id=viewer.viewer_id) is True
    finally:
        await privacy.close()

    assert await store.viewer_candidates.list_candidates(status=None) == []
    assert await store.viewers.approved_viewer_notes(viewer_ids=[viewer.viewer_id]) == []
    edits = await store.execute("SELECT action, before_text, after_text FROM memory_edits ORDER BY id")
    assert [(str(r["action"]), str(r["before_text"]), str(r["after_text"])) for r in edits] == [
        ("viewer_self_approve", "", ""),
        ("viewer_self_revoke", "", ""),
        ("viewer_self_approve", "", ""),
        ("viewer_self_reject", "", ""),
    ]
