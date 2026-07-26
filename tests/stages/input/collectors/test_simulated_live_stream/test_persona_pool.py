"""测试 PersonaPool"""

import asyncio

import pytest

from src.stages.input.collectors.simulated_live_stream.config_schema import (
    SimulatorConfigSchema,
)
from src.stages.input.collectors.simulated_live_stream.persona_pool import (
    PersonaPool,
)
from src.stages.input.collectors.simulated_live_stream.types import (
    PersonaRole,
)


@pytest.mark.asyncio
async def test_load_default_filters_hater():
    cfg = SimulatorConfigSchema()
    pool = PersonaPool()
    await pool.load(cfg)
    residents = pool.list_residents()
    roles = {p.role for p in residents}
    assert PersonaRole.HATER not in roles
    assert len(residents) == 9  # 10 - 1 hater


@pytest.mark.asyncio
async def test_load_enable_hater():
    cfg = SimulatorConfigSchema(enable_hater=True)
    pool = PersonaPool()
    await pool.load(cfg)
    assert len(pool.list_residents()) == 10


@pytest.mark.asyncio
async def test_pick_one_returns_valid_persona():
    cfg = SimulatorConfigSchema(enable_hater=True)
    pool = PersonaPool()
    await pool.load(cfg)
    p = pool.pick_one()
    assert p.user_id is not None
    assert p.user_nickname is not None
    assert p.role in PersonaRole


@pytest.mark.asyncio
async def test_record_message_increments():
    cfg = SimulatorConfigSchema(enable_hater=True)
    pool = PersonaPool()
    await pool.load(cfg)
    p = pool.pick_one()
    before = p.messages_generated
    pool.record_message(p)
    assert p.messages_generated == before + 1


@pytest.mark.asyncio
async def test_get_stats():
    cfg = SimulatorConfigSchema(enable_hater=True)
    pool = PersonaPool()
    await pool.load(cfg)
    block_count = 5
    for _ in range(block_count):
        p = pool.pick_one()
        pool.record_message(p)
    stats = pool.get_stats()
    assert isinstance(stats, dict)
    assert sum(stats.values()) == block_count


@pytest.mark.asyncio
async def test_generate_temporary_passerby():
    pool = PersonaPool()
    passerby = pool.generate_temporary_passerby()
    assert passerby.is_temporary is True
    assert passerby.role == PersonaRole.PASSERBY
    assert passerby.user_id.startswith("passerby_")


@pytest.mark.asyncio
async def test_passerby_pool_cap():
    pool = PersonaPool()
    for _ in range(60):
        pool.generate_temporary_passerby()
    assert len(pool._passersby) <= 50  # type: ignore[attr-defined]
