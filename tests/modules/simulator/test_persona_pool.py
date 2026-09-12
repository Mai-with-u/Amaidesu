"""测试 PersonaPool（SQLite 持久化后端）"""

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.simulator.config_schema import (
    SimulatorConfigSchema,
)
from src.modules.simulator.persona_pool import (
    PersonaPool,
)
from src.modules.simulator.types import (
    Persona,
    PersonaRole,
)
from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="persona-pool-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    s = SQLiteDatabase(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


async def _seed_residents(store: SQLiteDatabase, count: int = 10, include_hater: bool = True) -> None:
    """写入 count 个常驻人设（hater 仅 1 个）"""
    base_roles = ["fan", "teaser", "newcomer", "veteran"]
    for i in range(count):
        role = "hater" if include_hater and i == count - 1 else base_roles[i % len(base_roles)]
        await store.sim.insert_sim_persona(
            user_id=f"resident_{i:03d}",
            user_nickname=f"观众{i}",
            role=role,
            personality=f"测试人设{i}",
            speaking_style=f"简短{i}",
            fans_medal_level=i,
        )


@pytest.mark.asyncio
async def test_load_default_filters_hater(store):
    await _seed_residents(store)
    cfg = SimulatorConfigSchema()
    pool = PersonaPool(sim_repo=store.sim)
    await pool.load(cfg)
    residents = pool.list_residents()
    roles = {p.role for p in residents}
    assert PersonaRole.HATER not in roles
    assert len(residents) == 9  # 10 - 1 hater


@pytest.mark.asyncio
async def test_load_enable_hater(store):
    await _seed_residents(store)
    cfg = SimulatorConfigSchema(enable_hater=True)
    pool = PersonaPool(sim_repo=store.sim)
    await pool.load(cfg)
    assert len(pool.list_residents()) == 10


@pytest.mark.asyncio
async def test_load_empty_pool_when_no_data(store):
    cfg = SimulatorConfigSchema()
    pool = PersonaPool(sim_repo=store.sim)
    await pool.load(cfg)
    assert pool.list_residents() == []


@pytest.mark.asyncio
async def test_pick_one_falls_back_to_passerby_on_empty_pool(store):
    cfg = SimulatorConfigSchema()
    pool = PersonaPool(sim_repo=store.sim)
    await pool.load(cfg)
    p = pool.pick_one()
    assert p.is_temporary is True
    assert p.role == PersonaRole.PASSERBY


@pytest.mark.asyncio
async def test_pick_one_returns_valid_persona(store):
    await _seed_residents(store)
    cfg = SimulatorConfigSchema(enable_hater=True)
    pool = PersonaPool(sim_repo=store.sim)
    await pool.load(cfg)
    p = pool.pick_one()
    assert p.user_id is not None
    assert p.user_nickname is not None
    assert p.role in PersonaRole


@pytest.mark.asyncio
async def test_record_message_increments(store):
    await _seed_residents(store)
    cfg = SimulatorConfigSchema(enable_hater=True)
    pool = PersonaPool(sim_repo=store.sim)
    await pool.load(cfg)
    p = pool.pick_one()
    before = p.messages_generated
    pool.record_message(p)
    assert p.messages_generated == before + 1


@pytest.mark.asyncio
async def test_get_stats(store):
    await _seed_residents(store)
    cfg = SimulatorConfigSchema(enable_hater=True)
    pool = PersonaPool(sim_repo=store.sim)
    await pool.load(cfg)
    block_count = 5
    for _ in range(block_count):
        p = pool.pick_one()
        pool.record_message(p)
    stats = pool.get_stats()
    assert isinstance(stats, dict)
    assert sum(stats.values()) == block_count


def _new_pool(store: SQLiteDatabase) -> PersonaPool:
    return PersonaPool(sim_repo=store.sim)


@pytest.mark.asyncio
async def test_generate_temporary_passerby(store):
    pool = _new_pool(store)
    passerby = pool.generate_temporary_passerby()
    assert passerby.is_temporary is True
    assert passerby.role == PersonaRole.PASSERBY
    assert passerby.user_id.startswith("passerby_")


@pytest.mark.asyncio
async def test_passerby_pool_cap(store):
    pool = _new_pool(store)
    for _ in range(60):
        pool.generate_temporary_passerby()
    assert len(pool._passersby) <= 50  # type: ignore[attr-defined]


# --- 持久化管理（add/update/delete，写穿 DB） ---


def _new_persona(user_id: str, nickname: str) -> Persona:
    return Persona(
        user_id=user_id,
        user_nickname=nickname,
        role=PersonaRole.FAN,
        personality="测试性格",
        speaking_style="测试风格",
    )


@pytest.mark.asyncio
async def test_add_personas_persists(store):
    await _seed_residents(store)
    cfg = SimulatorConfigSchema()
    pool = _new_pool(store)
    await pool.load(cfg)
    added = await pool.add_personas([_new_persona("resident_new", "新观众")])
    assert added == 1

    pool2 = _new_pool(store)
    await pool2.load(cfg)
    ids = {p.user_id for p in pool2.list_residents()}
    assert "resident_new" in ids


@pytest.mark.asyncio
async def test_update_persona_persists(store):
    await _seed_residents(store)
    cfg = SimulatorConfigSchema()
    pool = _new_pool(store)
    await pool.load(cfg)
    assert await pool.update_persona("resident_000", {"user_nickname": "改名观众", "fans_medal_level": 30}) is True

    pool2 = _new_pool(store)
    await pool2.load(cfg)
    updated = next(p for p in pool2.list_residents() if p.user_id == "resident_000")
    assert updated.user_nickname == "改名观众"
    assert updated.fans_medal_level == 30


@pytest.mark.asyncio
async def test_update_persona_not_found(store):
    await _seed_residents(store)
    cfg = SimulatorConfigSchema()
    pool = _new_pool(store)
    await pool.load(cfg)
    assert await pool.update_persona("nonexistent", {"user_nickname": "x"}) is False


@pytest.mark.asyncio
async def test_delete_persona_persists(store):
    await _seed_residents(store)
    cfg = SimulatorConfigSchema()
    pool = _new_pool(store)
    await pool.load(cfg)
    assert await pool.delete_persona("resident_000") is True
    assert await pool.delete_persona("resident_000") is False

    pool2 = _new_pool(store)
    await pool2.load(cfg)
    ids = {p.user_id for p in pool2.list_residents()}
    assert "resident_000" not in ids


@pytest.mark.asyncio
async def test_empty_pool_add_then_load(store):
    cfg = SimulatorConfigSchema()
    pool = _new_pool(store)
    await pool.load(cfg)
    added = await pool.add_personas([_new_persona("resident_a", "观众甲"), _new_persona("resident_b", "观众乙")])
    assert added == 2
    assert len(pool.list_residents()) == 2

    pool2 = _new_pool(store)
    await pool2.load(cfg)
    assert len(pool2.list_residents()) == 2
