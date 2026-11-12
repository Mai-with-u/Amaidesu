"""出道场彩排观众种子：补进常驻人设、重复调用不重复插入、运营停用过的不复活。"""

from __future__ import annotations

from src.modules.simulator.seed_data import DEBUT_PERSONAS, seed_debut_personas
from src.modules.storage.database import SQLiteDatabase


async def test_seed_debut_personas_is_idempotent(store: SQLiteDatabase) -> None:
    assert await seed_debut_personas(store.sim) == len(DEBUT_PERSONAS)
    assert await seed_debut_personas(store.sim) == 0
    ids = {p.user_id for p in await store.sim.list_sim_personas(include_inactive=True)}
    assert {item["user_id"] for item in DEBUT_PERSONAS} <= ids
