"""v34 → v35 迁移：游戏事件多一列"发起方"，旧行为空串（续播不认领发起方不明的委派），再跑一遍不报错。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v35_game_event_initiator import migrate as v35_migrate
from src.modules.storage.schema import SCHEMA_VERSION


def _to_v34(path: Path) -> None:
    """把新库的游戏事件表去掉那一列、版本记录退回 34，留一条旧委派受理行。"""
    raw = sqlite3.connect(path)
    try:
        raw.executescript(
            "ALTER TABLE game_events DROP COLUMN initiator;"
            "DELETE FROM schema_migrations WHERE version > 34;"
            "INSERT INTO live_sessions(started_at_ms, kind, updated_at_ms) VALUES (1000, 'live', 1000);"
            "INSERT INTO game_events(live_session_id, game, event_type, message, scene, timestamp_ms)"
            " VALUES (1, 'minecraft', 'delegation_accepted', '把门装好', 'deleg_1', 1100);"
        )
        raw.commit()
    finally:
        raw.close()


async def test_v34_database_gains_the_initiator_column_and_keeps_old_rows(tmp_path: Path) -> None:
    """旧库升级：列补上、旧委派行的发起方为空串、正文不动；再跑一遍迁移不报错。"""
    path = tmp_path / "data" / "v34.db"
    fresh = SQLiteDatabase(path)
    await fresh.initialize()
    await fresh.close()
    _to_v34(path)

    upgraded = SQLiteDatabase(path)
    await upgraded.initialize()
    try:
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
        [record] = await upgraded.events.list_session_delegations(live_session_id=1)
        assert (record.instruction, record.initiator, record.status) == ("把门装好", "", "accepted")
        with upgraded.manager.transaction() as conn:
            v35_migrate(conn)
    finally:
        await upgraded.close()
