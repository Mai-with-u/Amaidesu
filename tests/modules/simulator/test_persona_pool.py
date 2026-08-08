"""测试 PersonaPool"""

import asyncio

import pytest

from src.modules.simulator.config_schema import (
    SimulatorConfigSchema,
)
from src.modules.simulator.persona_pool import (
    PersonaPool,
)
from src.modules.simulator.types import (
    PersonaRole,
)


def _build_residents_toml(count: int = 10, include_hater: bool = True) -> str:
    """构造 residents.toml 内容（count 个居民，hater 仅 1 个）"""
    base_roles = ["fan", "teaser", "newcomer", "veteran"]
    lines = ["[residents]", ""]
    for i in range(count):
        if include_hater and i == count - 1:
            role = "hater"
        else:
            role = base_roles[i % len(base_roles)]
        lines += [
            "[[residents.items]]",
            f'user_id = "resident_{i:03d}"',
            f'user_nickname = "观众{i}"',
            f'role = "{role}"',
            f'personality = "测试人设{i}"',
            f'speaking_style = "简短{i}"',
            f"fans_medal_level = {i}",
            "guard_level = 0",
            "is_temporary = false",
            "",
        ]
    return "\n".join(lines)


@pytest.fixture
def data_dir_with_residents(tmp_path):
    """创建包含 10 个居民（含 1 个 hater）的数据目录"""
    data_dir = tmp_path / "data" / "simulator"
    data_dir.mkdir(parents=True)
    (data_dir / "residents.toml").write_text(_build_residents_toml(), encoding="utf-8")
    return data_dir


@pytest.fixture
def empty_data_dir(tmp_path):
    """创建空数据目录（无常驻人设）"""
    data_dir = tmp_path / "data" / "simulator"
    data_dir.mkdir(parents=True)
    return data_dir


@pytest.mark.asyncio
async def test_load_default_filters_hater(data_dir_with_residents):
    cfg = SimulatorConfigSchema()
    pool = PersonaPool(data_dir=data_dir_with_residents)
    await pool.load(cfg)
    residents = pool.list_residents()
    roles = {p.role for p in residents}
    assert PersonaRole.HATER not in roles
    assert len(residents) == 9  # 10 - 1 hater


@pytest.mark.asyncio
async def test_load_enable_hater(data_dir_with_residents):
    cfg = SimulatorConfigSchema(enable_hater=True)
    pool = PersonaPool(data_dir=data_dir_with_residents)
    await pool.load(cfg)
    assert len(pool.list_residents()) == 10


@pytest.mark.asyncio
async def test_load_empty_pool_when_no_data_file(empty_data_dir):
    cfg = SimulatorConfigSchema()
    pool = PersonaPool(data_dir=empty_data_dir)
    await pool.load(cfg)
    assert pool.list_residents() == []


@pytest.mark.asyncio
async def test_pick_one_falls_back_to_passerby_on_empty_pool(empty_data_dir):
    cfg = SimulatorConfigSchema()
    pool = PersonaPool(data_dir=empty_data_dir)
    await pool.load(cfg)
    p = pool.pick_one()
    assert p.is_temporary is True
    assert p.role == PersonaRole.PASSERBY


@pytest.mark.asyncio
async def test_pick_one_returns_valid_persona(data_dir_with_residents):
    cfg = SimulatorConfigSchema(enable_hater=True)
    pool = PersonaPool(data_dir=data_dir_with_residents)
    await pool.load(cfg)
    p = pool.pick_one()
    assert p.user_id is not None
    assert p.user_nickname is not None
    assert p.role in PersonaRole


@pytest.mark.asyncio
async def test_record_message_increments(data_dir_with_residents):
    cfg = SimulatorConfigSchema(enable_hater=True)
    pool = PersonaPool(data_dir=data_dir_with_residents)
    await pool.load(cfg)
    p = pool.pick_one()
    before = p.messages_generated
    pool.record_message(p)
    assert p.messages_generated == before + 1


@pytest.mark.asyncio
async def test_get_stats(data_dir_with_residents):
    cfg = SimulatorConfigSchema(enable_hater=True)
    pool = PersonaPool(data_dir=data_dir_with_residents)
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
