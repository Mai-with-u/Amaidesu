"""v49 → v50 存储迁移与 LearnedSkillRepo 单元测试（只用 tmp_path / :memory:，绝不触碰真实库）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v50_agent_learned_skills import migrate as v50_migrate
from src.modules.storage.schema import SCHEMA_VERSION


def test_v50_migration_callback_creates_table_and_index_idempotently() -> None:
    """回调级验证：在内存库重复执行两次 v50 迁移，表结构、索引与唯一约束完好且幂等。"""
    conn = sqlite3.connect(":memory:")
    try:
        for _ in range(2):
            v50_migrate(conn)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert "agent_learned_skills" in tables
            cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_learned_skills)")}
            assert cols == {"id", "game", "save_id", "scope", "name", "trigger", "content", "updated_at_ms"}

        # 唯一约束 (game, scope, save_id, name) 生效（global 存空 save_id）
        conn.execute(
            "INSERT INTO agent_learned_skills(game, save_id, scope, name, trigger, content, updated_at_ms)"
            " VALUES ('demo_game', '', 'global', 'smelt_batch', '批量烧炼', '放入进料箱', 1000)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO agent_learned_skills(game, save_id, scope, name, trigger, content, updated_at_ms)"
                " VALUES ('demo_game', '', 'global', 'smelt_batch', '重复条目', '重复内容', 2000)"
            )
    finally:
        conn.close()


async def test_v49_db_upgrades_to_v50_idempotently_and_repo_crud_works(db_path: Path) -> None:
    """整链级验证：v49 旧库自动升级到 v50 补建 agent_learned_skills，二次启动幂等且仓储按作用域与存档隔离。"""
    # 先初始化再回退到 v49 并删表，模拟真实 v49 存量库
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        await db.execute("DELETE FROM schema_migrations WHERE version >= 50")
        await db.execute("DROP TABLE IF EXISTS agent_learned_skills")
        assert await db.get_schema_version() == 49
        assert await db.table_exists("agent_learned_skills") is False
    finally:
        await db.close()

    # 首次重开：触发 v49 → v50 升级，补建表并推进版本
    upgraded = SQLiteDatabase(db_path)
    await upgraded.initialize()
    try:
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
        assert await upgraded.table_exists("agent_learned_skills") is True
        await upgraded.assert_schema_ready()

        # 写入 global（强制存空 save_id）与两个存档各自的 world 技能
        rec_global = await upgraded.learned_skills.upsert_skill(
            game="sandbox_game",
            scope="global",
            save_id="ignored_save_id",
            name="waterwheel_setup",
            trigger="搭建水车动力时",
            content="水流从侧面顺向冲过水车叶片",
            updated_at_ms=10_000,
        )
        assert rec_global.save_id == ""
        assert rec_global.scope == "global"

        rec_world_a = await upgraded.learned_skills.upsert_skill(
            game="sandbox_game",
            scope="world",
            save_id="save-A",
            name="camp_smelter",
            trigger="营地批量烧矿时",
            content="投入左箱，右箱取成品",
            updated_at_ms=11_000,
        )
        await upgraded.learned_skills.upsert_skill(
            game="sandbox_game",
            scope="world",
            save_id="save-B",
            name="camp_smelter",
            trigger="二周目熔炉",
            content="投入上箱，下箱取成品",
            updated_at_ms=12_000,
        )
        assert rec_world_a.save_id == "save-A"

        # 覆盖更新同 (game, scope, save_id, name) 不增行
        updated_a = await upgraded.learned_skills.upsert_skill(
            game="sandbox_game",
            scope="world",
            save_id="save-A",
            name="camp_smelter",
            trigger="营地自动烧矿时",
            content="投入左箱，避开食物，右箱取成品",
            updated_at_ms=13_000,
        )
        assert updated_a.id == rec_world_a.id
        assert "避开食物" in updated_a.content
        assert await upgraded.learned_skills.count_skills(game="sandbox_game", scope="world", save_id="save-A") == 1
        assert await upgraded.learned_skills.count_skills(game="sandbox_game", scope="global") == 1

        # 列表按存档隔离：save-A 看得到自己的 world + global，看不到 save-B
        listed_a = await upgraded.learned_skills.list_skills(game="sandbox_game", save_id="save-A")
        assert [(item.scope, item.name, item.save_id) for item in listed_a] == [
            ("world", "camp_smelter", "save-A"),
            ("global", "waterwheel_setup", ""),
        ]
        # 未指定 scope 读取同名时：world 优先于 global
        await upgraded.learned_skills.upsert_skill(
            game="sandbox_game",
            scope="world",
            save_id="save-A",
            name="waterwheel_setup",
            trigger="本服水车改造版",
            content="用齿轮箱提速",
            updated_at_ms=14_000,
        )
        resolved = await upgraded.learned_skills.get_skill(
            game="sandbox_game", name="waterwheel_setup", save_id="save-A"
        )
        assert resolved is not None and resolved.scope == "world" and "齿轮箱" in resolved.content
        resolved_b = await upgraded.learned_skills.get_skill(
            game="sandbox_game", name="waterwheel_setup", save_id="save-B"
        )
        assert resolved_b is not None and resolved_b.scope == "global"

        # 删除本服同名后仍保留 global
        assert (
            await upgraded.learned_skills.delete_skill(
                game="sandbox_game", name="waterwheel_setup", save_id="save-A", scope="world"
            )
            is True
        )
        fallback = await upgraded.learned_skills.get_skill(
            game="sandbox_game", name="waterwheel_setup", save_id="save-A"
        )
        assert fallback is not None and fallback.scope == "global"
    finally:
        await upgraded.close()

    # 二次重启验证幂等与持久化保留
    reopened = SQLiteDatabase(db_path)
    await reopened.initialize()
    try:
        assert await reopened.get_schema_version() == SCHEMA_VERSION
        kept = await reopened.learned_skills.get_skill(game="sandbox_game", name="camp_smelter", save_id="save-A")
        assert kept is not None and "避开食物" in kept.content
    finally:
        await reopened.close()
