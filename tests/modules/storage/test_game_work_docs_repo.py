"""GameWorkDocsRepo（游戏工作文档仓储）与 v39 → v40 迁移。

覆盖：按 (game, save_id) 隔离读写、覆盖更新保留原存档名、move_world 不混写、
删场次时 updated_session_id 自动置 NULL 不丢文档、旧库升级到 v40 幂等。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.schema import SCHEMA_VERSION


async def test_work_docs_isolated_per_game_and_save_and_survives_session_deletion(db_path: Path) -> None:
    """不同游戏与不同存档互不串；删掉关联场次时外键置 NULL、文档仍完好。"""
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        session_id = await db.sessions.insert_live_session(
            stream_id="room-1", platform="bilibili", started_at_ms=1_000, title="第65场"
        )
        repo = db.game_work_docs
        await repo.save_work_docs(
            game="minecraft",
            save_id="save-home",
            save_name="麦窝",
            notebook="【刷铁机】北侧还差两格栏杆",
            todos=[{"content": "补齐北侧栏杆", "status": "in_progress"}],
            updated_session_id=session_id,
        )
        assert await repo.get_work_docs(game="minecraft", save_id="save-other") is None
        assert await repo.get_work_docs(game="text_adv", save_id="save-home") is None

        loaded = await repo.get_work_docs(game="minecraft", save_id="save-home")
        assert loaded is not None
        assert loaded.save_name == "麦窝"
        assert loaded.notebook == "【刷铁机】北侧还差两格栏杆"
        assert loaded.todos == [{"content": "补齐北侧栏杆", "status": "in_progress"}]
        assert loaded.updated_session_id == session_id

        # 覆盖写入时 save_name 为空不冲掉原先记下的存档名
        await repo.save_work_docs(
            game="minecraft",
            save_id="save-home",
            save_name="",
            notebook="【刷铁机】栏杆已完工",
            todos=[],
            updated_session_id=session_id,
        )
        updated = await repo.get_work_docs(game="minecraft", save_id="save-home")
        assert updated is not None and updated.save_name == "麦窝" and updated.todos == []

        # 删掉关联场次后 ON DELETE SET NULL，文档本身不丢失
        await db.execute("DELETE FROM live_sessions WHERE id = ?", (session_id,))
        after_del = await repo.get_work_docs(game="minecraft", save_id="save-home")
        assert after_del is not None
        assert after_del.notebook == "【刷铁机】栏杆已完工"
        assert after_del.updated_session_id is None
    finally:
        await db.close()


async def test_move_world_migrates_once_without_clobbering_existing_target(db_path: Path) -> None:
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        repo = db.game_work_docs
        await repo.save_work_docs(game="minecraft", save_id="legacy-key", notebook="旧键笔记")
        assert await repo.move_world(game="minecraft", from_save_id="legacy-key", to_save_id="world-uuid") == 1
        moved = await repo.get_work_docs(game="minecraft", save_id="world-uuid")
        assert moved is not None and moved.notebook == "旧键笔记"
        assert await repo.get_work_docs(game="minecraft", save_id="legacy-key") is None

        await repo.save_work_docs(game="minecraft", save_id="another-old", notebook="另一份")
        assert await repo.move_world(game="minecraft", from_save_id="another-old", to_save_id="world-uuid") == 0
    finally:
        await db.close()


async def test_v39_database_upgrades_to_v40_with_game_work_docs_table(db_path: Path) -> None:
    """v39 旧库缺 game_work_docs 表：重开升级到最新 SCHEMA_VERSION 并可正常读写。"""
    db = SQLiteDatabase(db_path)
    await db.initialize()
    await db.close()

    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE game_work_docs")
    conn.execute("DELETE FROM schema_migrations WHERE version >= 40")
    conn.commit()
    conn.close()

    upgraded = SQLiteDatabase(db_path)
    await upgraded.initialize()
    try:
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
        await upgraded.game_work_docs.save_work_docs(
            game="minecraft",
            save_id="save-home",
            notebook="升级后写入成功",
            todos=[{"content": "验收", "status": "done"}],
        )
    finally:
        await upgraded.close()

    reopened = SQLiteDatabase(db_path)
    await reopened.initialize()
    try:
        assert await reopened.get_schema_version() == SCHEMA_VERSION
        doc = await reopened.game_work_docs.get_work_docs(game="minecraft", save_id="save-home")
        assert doc is not None and doc.notebook == "升级后写入成功"
    finally:
        await reopened.close()
