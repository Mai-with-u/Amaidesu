"""GameCreatureRepo（游戏生物名册仓储）与 v21 → v22 迁移。

覆盖：按"游戏 + 世界"读写互不串、整行覆盖与删除同一事务、空写入不碰库；
旧库升级后多出名册表、再开一次零变更。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models import GameCreatureRecord
from src.modules.storage.schema import SCHEMA_VERSION

_MAID = GameCreatureRecord(
    creature_key="uuid:5f2b9c1e-0d4a-4c1b-9e7f-1a2b3c4d5e6f",
    type="touhou_little_maid:maid",
    kind="女仆",
    skin="莫莫酒狐",
    owner="你的",
    uuid="5f2b9c1e-0d4a-4c1b-9e7f-1a2b3c4d5e6f",
    last_seen_ms=1_000,
)


async def test_roster_is_kept_per_world_and_rewritten_by_changes(db_path: Path) -> None:
    """起名、死讯整行覆盖；别的世界、别的游戏读不到；取消路人名字时那一行删掉。"""
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        repo = db.game_creatures
        sheep = GameCreatureRecord(creature_key="uuid:a2", type="minecraft:sheep", nickname="咩咩", uuid="a2")
        await repo.save_creatures(game="minecraft", world_key="w1", creatures=[_MAID, sheep])
        assert await repo.list_creatures(game="minecraft", world_key="w2") == []
        assert await repo.list_creatures(game="text_adv", world_key="w1") == []

        dead = _MAID.model_copy(
            update={"nickname": "阿缕", "died_ms": 2_000, "death_note": "被苦力怕炸死了", "died_session_id": 52}
        )
        await repo.save_creatures(game="minecraft", world_key="w1", creatures=[dead], removed_keys=["uuid:a2"])
        assert await repo.list_creatures(game="minecraft", world_key="w1") == [dead]

        await repo.save_creatures(game="minecraft", world_key="w1", creatures=[])
        assert len(await repo.list_creatures(game="minecraft", world_key="w1")) == 1
    finally:
        await db.close()


async def test_v21_database_gains_creature_table(db_path: Path) -> None:
    """旧库没有名册表：升级后表在、版本到最新、能读写；再开一次版本与数据都不变。"""
    db = SQLiteDatabase(db_path)
    await db.initialize()
    await db.close()
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE game_creatures")
    conn.execute("DELETE FROM schema_migrations WHERE version > 21")
    conn.commit()
    conn.close()

    upgraded = SQLiteDatabase(db_path)
    await upgraded.initialize()
    try:
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
        await upgraded.game_creatures.save_creatures(game="minecraft", world_key="", creatures=[_MAID])
    finally:
        await upgraded.close()

    reopened = SQLiteDatabase(db_path)
    await reopened.initialize()
    try:
        assert await reopened.get_schema_version() == SCHEMA_VERSION
        assert await reopened.game_creatures.list_creatures(game="minecraft", world_key="") == [_MAID]
    finally:
        await reopened.close()
