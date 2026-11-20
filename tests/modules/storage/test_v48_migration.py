"""v47 → v48 迁移：game_work_docs 新增 pinned_rules 持久钉住规矩列（回调幂等 + 整链升级 + 备份验证）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v48_game_work_docs_pinned_rules import migrate as v48_migrate
from src.modules.storage.schema import SCHEMA_VERSION, build_schema_sql


def _seed_v47_database(path: Path) -> None:
    """用去掉 game_work_docs.pinned_rules 列的 v47 形状 DDL 建库，并写入存量工作文档行。"""
    v47_ddl = build_schema_sql().replace(
        "    todos              TEXT NOT NULL DEFAULT '[]',\n    pinned_rules       TEXT NOT NULL DEFAULT '[]',\n",
        "    todos              TEXT NOT NULL DEFAULT '[]',\n",
    )
    table_block = v47_ddl.split("CREATE TABLE IF NOT EXISTS game_work_docs")[1].split(");")[0]
    assert "pinned_rules" not in table_block

    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(v47_ddl)
        for version in range(1, 48):
            conn.execute("INSERT INTO schema_migrations(version, applied_at_ms) VALUES (?, ?)", (version, 1_000))
        conn.execute(
            "INSERT INTO live_sessions(id, stream_id, platform, kind, started_at_ms, ended_at_ms, updated_at_ms)"
            " VALUES (1, 'room_1', 'bilibili', 'live', 1000, 2000, 2000)"
        )
        conn.execute(
            "INSERT INTO game_work_docs(game, save_id, save_name, notebook, todos, updated_session_id, updated_at_ms)"
            " VALUES ('minecraft', 'save-home', '机械动力服', '旧笔记：风车建在东坡', '[{\"content\":\"补齿轮\",\"status\":\"pending\"}]', 1, 1800)"
        )
        conn.commit()
    finally:
        conn.close()


def test_v48_migrate_callback_is_idempotent_on_empty_and_v47_tables(tmp_path: Path) -> None:
    """回调级幂等：空库不报错；v47 表连续执行两次 v48_migrate 结果一致且不丢旧数据。"""
    empty_conn = sqlite3.connect(":memory:")
    try:
        v48_migrate(empty_conn)
        v48_migrate(empty_conn)
    finally:
        empty_conn.close()

    db_file = tmp_path / "callback_v47.db"
    _seed_v47_database(db_file)
    conn = sqlite3.connect(db_file)
    try:
        v48_migrate(conn)
        v48_migrate(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(game_work_docs)").fetchall()}
        assert "pinned_rules" in cols
        row = conn.execute(
            "SELECT notebook, todos, pinned_rules FROM game_work_docs WHERE game='minecraft' AND save_id='save-home'"
        ).fetchone()
        assert row == ("旧笔记：风车建在东坡", '[{"content":"补齿轮","status":"pending"}]', "[]")
    finally:
        conn.close()


async def test_v47_database_upgrades_to_v48_keeping_docs_and_supporting_pinned_rules(tmp_path: Path) -> None:
    """整链级升级：v47 旧库自动备份并推进到 SCHEMA_VERSION，保留既有工作文档并支持 pinned_rules 读写，重开幂等。"""
    db_file = tmp_path / "upgrade_v47.db"
    _seed_v47_database(db_file)

    db = SQLiteDatabase(db_file)
    await db.initialize()
    try:
        assert SCHEMA_VERSION >= 48
        assert await db.get_schema_version() == SCHEMA_VERSION
        await db.assert_schema_ready()

        loaded = await db.game_work_docs.get_work_docs(game="minecraft", save_id="save-home")
        assert loaded is not None
        assert loaded.save_name == "机械动力服"
        assert loaded.notebook == "旧笔记：风车建在东坡"
        assert loaded.todos == [{"content": "补齿轮", "status": "pending"}]
        assert loaded.pinned_rules == []

        await db.game_work_docs.save_work_docs(
            game="minecraft",
            save_id="save-home",
            notebook=loaded.notebook,
            todos=loaded.todos,
            pinned_rules=["公共储物间箱子禁止拆改", "不要动东坡风车主轴"],
            updated_session_id=1,
        )
        updated = await db.game_work_docs.get_work_docs(game="minecraft", save_id="save-home")
        assert updated is not None
        assert updated.pinned_rules == ["公共储物间箱子禁止拆改", "不要动东坡风车主轴"]
    finally:
        await db.close()

    backups = list((tmp_path / "backups").glob(f"*-pre-v{SCHEMA_VERSION}-*.db"))
    assert len(backups) == 1

    # 二次打开已是当前版本：幂等无副作用，不再生成额外备份
    reopened = SQLiteDatabase(db_file)
    await reopened.initialize()
    try:
        assert await reopened.get_schema_version() == SCHEMA_VERSION
        doc = await reopened.game_work_docs.get_work_docs(game="minecraft", save_id="save-home")
        assert doc is not None
        assert doc.pinned_rules == ["公共储物间箱子禁止拆改", "不要动东坡风车主轴"]
    finally:
        await reopened.close()
    assert len(list((tmp_path / "backups").glob("*-pre-v*.db"))) == 1
