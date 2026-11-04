"""v15 迁移单测：观众事实表退役。

场次回顾表随 v15 上线，但旧记忆整体退役后已由 v25 删除；本文件只覆盖
v15 回调本身的行为（删 ``viewer_facts``，不动其它表），重复执行幂等。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v15_session_recaps_drop_viewer_facts import migrate as v15_migrate
from src.modules.storage.schema import SCHEMA_VERSION, list_expected_tables


async def test_fresh_database_has_no_viewer_facts(tmp_path: Path) -> None:
    """新库：viewer_facts 不再建表，也不在启动闸门清单里。"""
    assert SCHEMA_VERSION >= 15
    assert "viewer_facts" not in list_expected_tables()
    db = SQLiteDatabase(tmp_path / "fresh.db")
    await db.initialize()
    try:
        assert await db.table_exists("viewer_facts") is False
    finally:
        await db.close()


def test_v14_database_upgrade_drops_facts_keeps_other_tables(tmp_path: Path) -> None:
    """v14 形状旧库：事实表被删，其它表与存量数据原样保留，回调幂等。"""
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.executescript(
        """
        CREATE TABLE viewer_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, user_id TEXT NOT NULL,
            fact_text TEXT NOT NULL, source_message_id TEXT NOT NULL DEFAULT '', created_at_ms INTEGER NOT NULL
        );
        CREATE TABLE viewer_names (
            id INTEGER PRIMARY KEY AUTOINCREMENT, viewer_id INTEGER NOT NULL, user_name TEXT NOT NULL,
            first_seen_ms INTEGER NOT NULL, last_seen_ms INTEGER NOT NULL, UNIQUE(viewer_id, user_name)
        );
        INSERT INTO viewer_facts(platform, user_id, fact_text, created_at_ms) VALUES ('bilibili', 'u1', '喜欢数码', 1);
        INSERT INTO viewer_names(viewer_id, user_name, first_seen_ms, last_seen_ms) VALUES (1, '甲', 1, 2);
        """
    )

    v15_migrate(conn)
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='viewer_facts'").fetchone() is None
    assert conn.execute("SELECT user_name FROM viewer_names WHERE viewer_id=1").fetchone()[0] == "甲"

    # 幂等：再跑一次不报错、无关表仍在
    v15_migrate(conn)
    assert conn.execute("SELECT COUNT(*) FROM viewer_names").fetchone()[0] == 1
    conn.close()
