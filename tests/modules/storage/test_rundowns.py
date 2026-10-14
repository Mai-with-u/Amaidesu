"""rundowns 表 CRUD 与 v5 → v6 迁移（agenda 两表 DROP，rundowns 新建）"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage import SCHEMA_VERSION, list_expected_tables
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.rundown import DEFAULT_RUNDOWN, Rundown, RundownSegment
from src.modules.storage.schema import build_schema_sql
from src.modules.time_utils import now_ms


def _make_rundown(rundown_id: str, title: str = "测试流程单", expected_ms: int = 10_000) -> Rundown:
    segment = RundownSegment(id="s1", title="开场", task_description="打招呼", expected_ms=expected_ms)
    return Rundown(rundown_id=rundown_id, title=title, segments=[segment])


async def test_rundown_crud_round_trip_in_creation_order(store: SQLiteDatabase) -> None:
    repo = store.rundowns
    assert await repo.get_rundown("rd_1") is None
    for rid in ("rd_1", "rd_2", "rd_3"):
        await repo.upsert_rundown(_make_rundown(rid, title=f"流程单 {rid}"))

    fetched = await repo.get_rundown("rd_1")
    assert (fetched.title, fetched.segments[0].id, fetched.segments[0].expected_ms) == ("流程单 rd_1", "s1", 10_000)
    assert [r.rundown_id for r in await repo.list_rundowns()] == ["rd_1", "rd_2", "rd_3"]  # created_at_ms 升序

    assert await repo.delete_rundown("rd_1") is True
    assert await repo.delete_rundown("rd_1") is False
    assert [r.rundown_id for r in await repo.list_rundowns()] == ["rd_2", "rd_3"]


async def test_upsert_preserves_created_at_and_refreshes_updated_at(store: SQLiteDatabase) -> None:
    sql = "SELECT created_at_ms, updated_at_ms FROM rundowns WHERE id='rd_1'"
    await store.rundowns.upsert_rundown(_make_rundown("rd_1"))
    first = (await store.execute(sql))[0]
    while now_ms() <= first["updated_at_ms"]:
        pass  # 跨过毫秒边界，避免快机器同毫秒

    await store.rundowns.upsert_rundown(_make_rundown("rd_1", title="更新后的标题", expected_ms=20_000))
    second = (await store.execute(sql))[0]
    assert second["created_at_ms"] == first["created_at_ms"]
    assert second["updated_at_ms"] > first["updated_at_ms"]
    fetched = await store.rundowns.get_rundown("rd_1")
    assert (fetched.title, fetched.segments[0].expected_ms) == ("更新后的标题", 20_000)


async def test_segments_serialization_round_trip(store: SQLiteDatabase) -> None:
    complex_rundown = Rundown(
        rundown_id="rd_complex",
        title="复杂流程单",
        segments=[
            RundownSegment(
                id="s1",
                title="开场",
                task_description="打招呼",
                key_points=["问好", "自我介绍"],
                expected_ms=60_000,
                min_duration_ms=30_000,
                notes="参考开场白",
            ),
            RundownSegment(id="s2", title="聊天", task_description="和观众互动", expected_ms=300_000),
        ],
    )
    for rundown in (complex_rundown, DEFAULT_RUNDOWN):
        await store.rundowns.upsert_rundown(rundown)
        assert await store.rundowns.get_rundown(rundown.rundown_id) == rundown


async def test_fresh_db_has_rundowns_and_no_agenda_tables(store: SQLiteDatabase) -> None:
    sql = build_schema_sql()
    assert "CREATE TABLE IF NOT EXISTS rundowns" in sql and "agenda_" not in sql
    assert "rundowns" in list_expected_tables()
    assert not any(t.startswith("agenda_") for t in list_expected_tables())
    tables = await store.list_tables()
    assert "rundowns" in tables and not any(t.startswith("agenda_") for t in tables)


async def test_v5_to_v6_migration_is_idempotent(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE agenda_plan (id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL);
        CREATE TABLE agenda_runtime (id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL);
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at_ms INTEGER NOT NULL);
        INSERT INTO schema_migrations(version, applied_at_ms) VALUES (5, 0);
        """
    )
    conn.commit()
    conn.close()

    for round_no in range(2):
        db = SQLiteDatabase(db_path)
        await db.initialize()
        try:
            assert await db.get_schema_version() == SCHEMA_VERSION
            assert not await db.table_exists("agenda_plan") and not await db.table_exists("agenda_runtime")
            versions = [r["version"] for r in await db.execute("SELECT version FROM schema_migrations")]
            assert len(versions) == len(set(versions))
            if round_no == 0:
                cols = {row["name"]: row for row in await db.execute("PRAGMA table_info(rundowns)")}
                assert (cols["id"]["type"], cols["id"]["pk"]) == ("TEXT", 1)
                for name in ("title", "segments_json", "created_at_ms", "updated_at_ms"):
                    assert cols[name]["notnull"] == 1
                await db.rundowns.upsert_rundown(_make_rundown("keep_me"))
            else:
                assert (await db.rundowns.get_rundown("keep_me")).rundown_id == "keep_me"
        finally:
            await db.close()
