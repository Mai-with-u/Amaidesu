"""删除模拟观众的输入窗口列时，现有观众资料与统计保留。"""

from pathlib import Path

from src.modules.storage.database import SQLiteDatabase


async def test_existing_persona_survives_window_column_removal(tmp_path: Path) -> None:
    """旧库重新初始化后移除窗口列，重复初始化仍能读取同一位观众。"""
    path = tmp_path / "simulator.db"
    store = SQLiteDatabase(path)
    await store.initialize()
    await store.sim.insert_sim_persona(
        user_id="viewer",
        user_nickname="老观众",
        role="veteran",
        personality="耐心",
        speaking_style="简洁",
        messages_generated=23,
    )
    await store.execute("ALTER TABLE sim_personas ADD COLUMN context_window_size INTEGER")
    await store.execute("UPDATE sim_personas SET context_window_size=5")
    await store.execute("DELETE FROM schema_migrations WHERE version=11")
    await store.close()
    for _ in range(2):
        reopened = SQLiteDatabase(path)
        try:
            await reopened.initialize()
            columns = await reopened.execute("PRAGMA table_info(sim_personas)")
            assert "context_window_size" not in {row["name"] for row in columns}
            rows = await reopened.sim.list_sim_personas()
            assert len(rows) == 1 and rows[0]["user_nickname"] == "老观众"
            assert rows[0]["messages_generated"] == 23
            assert await reopened.get_schema_version() == 11
        finally:
            await reopened.close()
