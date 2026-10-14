"""sim_personas / sim_gifts 表 CRUD 与启动期种子导入"""

from __future__ import annotations

import sqlite3

import pytest

from src.modules.simulator.seed_data import DEFAULT_GIFTS, DEFAULT_PERSONAS, seed_simulator_data
from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
def sim(store: SQLiteDatabase):
    return store.sim


async def _persona(sim, user_id: str, **kwargs) -> None:
    kwargs.setdefault("user_nickname", user_id)
    await sim.insert_sim_persona(user_id=user_id, role="fan", personality="p", speaking_style="s", **kwargs)


async def test_persona_crud(sim) -> None:
    await _persona(sim, "sim_a")
    await _persona(sim, "sim_b", is_active=False)
    # 停用行默认不可见
    assert [row["user_id"] for row in await sim.list_sim_personas()] == ["sim_a"]
    assert {row["user_id"] for row in await sim.list_sim_personas(include_inactive=True)} == {"sim_a", "sim_b"}
    with pytest.raises(sqlite3.IntegrityError):
        await _persona(sim, "sim_a")

    before = (await sim.list_sim_personas())[0]
    assert await sim.update_sim_persona(user_id="sim_a", fields={"user_nickname": "新名"}) is True
    row = (await sim.list_sim_personas())[0]
    assert row["user_nickname"] == "新名" and row["updated_at_ms"] >= before["updated_at_ms"]
    with pytest.raises(ValueError, match="非法字段"):
        await sim.update_sim_persona(user_id="sim_a", fields={"user_id": "hack"})
    assert await sim.update_sim_persona(user_id="ghost", fields={"personality": "x"}) is False

    assert await sim.delete_sim_persona(user_id="sim_a") is True
    assert await sim.delete_sim_persona(user_id="sim_a") is False
    assert await sim.count_sim_personas() == 1


async def test_gift_crud(sim) -> None:
    await sim.insert_sim_gift(gift_id="g1", gift_name="辣条", category="normal", weight=10, data_type="gift")
    await sim.insert_sim_gift(
        gift_id="sc1", gift_name="SC 50元", category="sc", weight=1, data_type="super_chat", sc_amount_rmb=50
    )
    assert {r["gift_id"]: r["sc_amount_rmb"] for r in await sim.list_sim_gifts()} == {"g1": None, "sc1": 50}

    assert await sim.update_sim_gift(gift_id="g1", fields={"weight": 20}) is True
    assert (await sim.list_sim_gifts())[0]["weight"] == 20
    with pytest.raises(ValueError, match="非法字段"):
        await sim.update_sim_gift(gift_id="g1", fields={"gift_id": "hack"})
    assert await sim.delete_sim_gift(gift_id="g1") is True
    assert await sim.count_sim_gifts() == 1


async def test_seed_imports_defaults_once(sim) -> None:
    await seed_simulator_data(sim)
    await seed_simulator_data(sim)  # 非空表不追加
    assert await sim.count_sim_personas() == len(DEFAULT_PERSONAS)
    assert await sim.count_sim_gifts() == len(DEFAULT_GIFTS)
    assert {row["user_id"] for row in await sim.list_sim_personas()} == {p["user_id"] for p in DEFAULT_PERSONAS}


async def test_seed_skips_existing_user_data(sim) -> None:
    await _persona(sim, "my_own")
    await seed_simulator_data(sim)
    assert [row["user_id"] for row in await sim.list_sim_personas()] == ["my_own"]
