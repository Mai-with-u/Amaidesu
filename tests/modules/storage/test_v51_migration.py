"""v50 → v51 存储迁移与 MemoryCorrectionRepo progress/list_commitment_updates 单元测试。

严格只用 tmp_path / :memory:，绝不触碰真实库 data/amaidesu.db。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v51_memory_correction_progress import migrate as v51_migrate
from src.modules.storage.schema import SCHEMA_VERSION

# v50 形态的 memory_corrections 建表语句（verdict CHECK 尚未包含 'progress'）
_V50_MEMORY_CORRECTIONS_SQL = """
CREATE TABLE memory_corrections (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id        INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    target_kind            TEXT NOT NULL CHECK (target_kind IN ('archive', 'commitment', 'general')),
    archive_line_id        INTEGER REFERENCES archive_lines(id) ON DELETE CASCADE,
    commitment_id          INTEGER REFERENCES commitments(id) ON DELETE CASCADE,
    verdict                TEXT NOT NULL CHECK (verdict IN ('done', 'invalid', 'wrong', 'note')),
    summary                TEXT NOT NULL,
    evidence_kind          TEXT NOT NULL CHECK (evidence_kind IN ('game_receipt', 'player_chat', 'operator', 'self_seen')),
    evidence_quote         TEXT NOT NULL DEFAULT '',
    evidence_game_chat_id  INTEGER REFERENCES game_chat(id) ON DELETE CASCADE,
    source                 TEXT NOT NULL CHECK (source IN ('streamer', 'operator')),
    prev_commitment_status TEXT NOT NULL DEFAULT '',
    created_at_ms          INTEGER NOT NULL,
    reverted_at_ms         INTEGER,
    settled_disposition    TEXT NOT NULL DEFAULT ''
)
""".strip()

_V50_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_memory_corrections_session ON memory_corrections(live_session_id, created_at_ms)"
)


def _seed_minimal_parents(conn: sqlite3.Connection) -> None:
    """在内存库构造 memory_corrections 依赖的四张父表并写入最小父行。"""
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE live_sessions (id INTEGER PRIMARY KEY);
        CREATE TABLE archive_lines (id INTEGER PRIMARY KEY);
        CREATE TABLE commitments (id INTEGER PRIMARY KEY);
        CREATE TABLE game_chat (id INTEGER PRIMARY KEY);
        INSERT INTO live_sessions(id) VALUES (1);
        INSERT INTO archive_lines(id) VALUES (10);
        INSERT INTO commitments(id) VALUES (20);
        INSERT INTO game_chat(id) VALUES (30);
        """
    )


def test_v51_migration_callback_rebuilds_check_preserves_rows_fks_index_and_is_idempotent() -> None:
    """回调级验证：重建表放宽 verdict CHECK 允许 'progress'，保留存量行、外键级联与索引，二次执行严格幂等。"""
    conn = sqlite3.connect(":memory:")
    try:
        _seed_minimal_parents(conn)
        conn.execute(_V50_MEMORY_CORRECTIONS_SQL)
        conn.execute(_V50_INDEX_SQL)

        # 播种存量行（含各类外键引用与非连续 id）
        conn.execute(
            "INSERT INTO memory_corrections("
            " id, live_session_id, target_kind, archive_line_id, commitment_id, verdict, summary,"
            " evidence_kind, evidence_quote, evidence_game_chat_id, source, prev_commitment_status,"
            " created_at_ms, reverted_at_ms, settled_disposition"
            ") VALUES"
            " (5, 1, 'archive', 10, NULL, 'invalid', '旧档案不成立', 'operator', '已换档', NULL, 'streamer', '', 1000, NULL, 'applied'),"
            " (9, 1, 'commitment', NULL, 20, 'done', '旧承诺已完成', 'player_chat', '收到了', 30, 'streamer', 'open', 2000, NULL, '')"
        )

        # 迁移前插入 'progress' 必须被旧 CHECK 拦下
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO memory_corrections("
                " live_session_id, target_kind, commitment_id, verdict, summary,"
                " evidence_kind, source, created_at_ms"
                ") VALUES (1, 'commitment', 20, 'progress', '还了一半', 'game_receipt', 'streamer', 3000)"
            )

        # 连续执行两次：第一次重建表，第二次因 DDL 已含 'progress' 直接跳过
        v51_migrate(conn)
        ddl_after_first = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_corrections'"
        ).fetchone()[0]
        v51_migrate(conn)
        ddl_after_second = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_corrections'"
        ).fetchone()[0]
        assert "'progress'" in ddl_after_first
        assert ddl_after_second == ddl_after_first

        # 存量行完整保留（含原 id 与全部字段）
        rows = conn.execute(
            "SELECT id, target_kind, archive_line_id, commitment_id, verdict, summary, evidence_game_chat_id,"
            " prev_commitment_status, settled_disposition FROM memory_corrections ORDER BY id"
        ).fetchall()
        assert rows == [
            (5, "archive", 10, None, "invalid", "旧档案不成立", None, "", "applied"),
            (9, "commitment", None, 20, "done", "旧承诺已完成", 30, "open", ""),
        ]

        # 索引与四条 ON DELETE CASCADE 外键全部保留
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(memory_corrections)")}
        assert "idx_memory_corrections_session" in indexes
        fk_targets = {(row[2], row[3], row[6]) for row in conn.execute("PRAGMA foreign_key_list(memory_corrections)")}
        assert fk_targets == {
            ("live_sessions", "live_session_id", "CASCADE"),
            ("archive_lines", "archive_line_id", "CASCADE"),
            ("commitments", "commitment_id", "CASCADE"),
            ("game_chat", "evidence_game_chat_id", "CASCADE"),
        }

        # 迁移后支持 'progress'，仍拒绝非法 verdict
        conn.execute(
            "INSERT INTO memory_corrections("
            " live_session_id, target_kind, commitment_id, verdict, summary,"
            " evidence_kind, source, created_at_ms"
            ") VALUES (1, 'commitment', 20, 'progress', '已存入 2 颗钻石', 'game_receipt', 'streamer', 3000)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO memory_corrections("
                " live_session_id, target_kind, commitment_id, verdict, summary,"
                " evidence_kind, source, created_at_ms"
                ") VALUES (1, 'commitment', 20, 'bogus', '非法值', 'game_receipt', 'streamer', 3100)"
            )

        # 验证外键级联真实生效：删除承诺 20 时关联更正行随级联删除，档案行更正不受影响
        conn.execute("DELETE FROM commitments WHERE id=20")
        remaining_ids = [row[0] for row in conn.execute("SELECT id FROM memory_corrections ORDER BY id")]
        assert remaining_ids == [5]
    finally:
        conn.close()


async def test_v50_db_upgrades_to_v51_and_progress_repo_semantics(db_path: Path) -> None:
    """整链级验证：v50 旧库自动升至 v51、progress 保持承诺 open、只读查询按序过滤且支持撤回不误改已结案承诺。"""
    # 1. 初始化并回退到 v50 形态（旧 CHECK 约束 + 一条存量更正行）
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        session_id = await db.sessions.insert_live_session(started_at_ms=10_000, kind="live")
        comm_a = await db.commitments.insert_commitment(
            what="补还 PlayerA 三颗钻石",
            quote="下次还你三颗钻石",
            game="sandbox",
            created_session_id=session_id,
            now_ms_value=11_000,
        )
        comm_b = await db.commitments.insert_commitment(
            what="帮 PlayerB 修木桥",
            quote="等下帮你修桥",
            game="sandbox",
            created_session_id=session_id,
            now_ms_value=11_500,
        )
        assert comm_a is not None and comm_b is not None

        # 用旧 v50 DDL 替换 memory_corrections 并塞入一条存量运营提醒，版本回退到 50
        await db.execute("DROP TABLE memory_corrections")
        await db.execute(_V50_MEMORY_CORRECTIONS_SQL)
        await db.execute(_V50_INDEX_SQL)
        await db.execute(
            "INSERT INTO memory_corrections("
            " live_session_id, target_kind, verdict, summary, evidence_kind, source, created_at_ms"
            ") VALUES (?, 'general', 'note', '旧运营提醒', 'operator', 'operator', 12000)",
            (session_id,),
        )
        await db.execute("DELETE FROM schema_migrations WHERE version >= 51")
        assert await db.get_schema_version() == 50
    finally:
        await db.close()

    # 2. 重开触发真实 v50 → v51 升级
    upgraded = SQLiteDatabase(db_path)
    await upgraded.initialize()
    try:
        assert SCHEMA_VERSION >= 51
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
        await upgraded.assert_schema_ready()

        # 存量运营提醒保留完好
        existing = await upgraded.memory_corrections.list_active_by_session(live_session_id=session_id)
        assert [(item.target_kind, item.verdict, item.summary) for item in existing] == [
            ("general", "note", "旧运营提醒")
        ]

        # 写入 commitment progress：不改 commitments.status（保持 open），仅刷新 updated_at_ms，prev_commitment_status=""
        prog_1 = await upgraded.memory_corrections.insert_and_apply(
            live_session_id=session_id,
            target_kind="commitment",
            commitment_id=comm_a,
            verdict="progress",
            summary="已往 PlayerA 箱子存入 2 颗，还欠 1 颗",
            evidence_kind="game_receipt",
            evidence_quote="往 PlayerA 放的木桶存进了 2 个钻石",
            source="streamer",
            created_at_ms=20_000,
        )
        assert prog_1.verdict == "progress"
        assert prog_1.prev_commitment_status == ""
        comm_a_row = await upgraded.commitments.get_commitment(commitment_id=comm_a)
        assert comm_a_row is not None
        assert comm_a_row.status == "open"
        assert comm_a_row.close_source == ""
        assert comm_a_row.close_quote is None
        assert comm_a_row.updated_at_ms == 20_000

        # 写入另一条承诺的 done 结案
        done_b = await upgraded.memory_corrections.insert_and_apply(
            live_session_id=session_id,
            target_kind="commitment",
            commitment_id=comm_b,
            verdict="done",
            summary="木桥已全部铺完",
            evidence_kind="game_receipt",
            evidence_quote="木桥完工",
            source="streamer",
            created_at_ms=21_000,
        )
        assert done_b.prev_commitment_status == "open"
        comm_b_row = await upgraded.commitments.get_commitment(commitment_id=comm_b)
        assert comm_b_row is not None and comm_b_row.status == "closed" and comm_b_row.close_source == "correction"

        # 再对 comm_a 结案 done，确认随后撤回早期 progress 不会把已结案的 comm_a 误还原成 open
        done_a = await upgraded.memory_corrections.insert_and_apply(
            live_session_id=session_id,
            target_kind="commitment",
            commitment_id=comm_a,
            verdict="done",
            summary="最后 1 颗已扔给 PlayerA，全部还清",
            evidence_kind="game_receipt",
            evidence_quote="朝 PlayerA 扔出了 1 个钻石",
            source="streamer",
            created_at_ms=22_000,
        )

        # list_commitment_updates_by_session 只返回本场未撤回的 target_kind='commitment'，按 created_at_ms ASC, id ASC 排
        updates = await upgraded.memory_corrections.list_commitment_updates_by_session(live_session_id=session_id)
        assert [(item.id, item.commitment_id, item.verdict) for item in updates] == [
            (prog_1.id, comm_a, "progress"),
            (done_b.id, comm_b, "done"),
            (done_a.id, comm_a, "done"),
        ]

        # 撤回 prog_1：仅标 reverted_at_ms 并从只读列表退出，不改动已结案的 comm_a 状态
        assert await upgraded.memory_corrections.revert_correction(correction_id=prog_1.id, reverted_at_ms=23_000)
        comm_a_after_revert = await upgraded.commitments.get_commitment(commitment_id=comm_a)
        assert comm_a_after_revert is not None and comm_a_after_revert.status == "closed"

        updates_after = await upgraded.memory_corrections.list_commitment_updates_by_session(live_session_id=session_id)
        assert [item.id for item in updates_after] == [done_b.id, done_a.id]
    finally:
        await upgraded.close()

    # 3. 二次重启验证幂等与落盘保留
    reopened = SQLiteDatabase(db_path)
    await reopened.initialize()
    try:
        assert await reopened.get_schema_version() == SCHEMA_VERSION
        persisted = await reopened.memory_corrections.list_commitment_updates_by_session(live_session_id=session_id)
        assert [(item.commitment_id, item.verdict) for item in persisted] == [(comm_b, "done"), (comm_a, "done")]
    finally:
        await reopened.close()
