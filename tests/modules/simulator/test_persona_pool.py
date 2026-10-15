"""PersonaPool（SQLite 持久化后端）：常驻人设加载与 hater 过滤、抽取与路人兜底、增删改写穿数据库"""

import pytest

from src.modules.simulator.config_schema import SimulatorConfigSchema
from src.modules.simulator.persona_pool import PersonaPool
from src.modules.simulator.types import Persona, PersonaRole
from src.modules.storage.database import SQLiteDatabase


async def _seed_residents(store: SQLiteDatabase, count: int = 10) -> None:
    """写入 count 个常驻人设，最后一个是 hater。"""
    roles = ["fan", "teaser", "newcomer", "veteran"]
    for i in range(count):
        await store.sim.insert_sim_persona(
            user_id=f"resident_{i:03d}",
            user_nickname=f"观众{i}",
            role="hater" if i == count - 1 else roles[i % len(roles)],
            personality=f"测试人设{i}",
            speaking_style=f"简短{i}",
            fans_medal_level=i,
        )


async def _loaded(store: SQLiteDatabase, **cfg) -> PersonaPool:
    pool = PersonaPool(sim_repo=store.sim)
    await pool.load(SimulatorConfigSchema(**cfg))
    return pool


def _persona(user_id: str, nickname: str) -> Persona:
    return Persona(
        user_id=user_id, user_nickname=nickname, role=PersonaRole.FAN, personality="测试性格", speaking_style="测试风格"
    )


@pytest.mark.parametrize(("enable_hater", "expected"), [(False, 9), (True, 10)])
async def test_load_filters_hater_unless_enabled(store: SQLiteDatabase, enable_hater: bool, expected: int) -> None:
    await _seed_residents(store)
    pool = await _loaded(store, enable_hater=enable_hater)
    residents = pool.list_residents()
    assert len(residents) == expected
    assert (PersonaRole.HATER in {p.role for p in residents}) is enable_hater


async def test_pick_one_returns_resident_counts_messages_and_falls_back_to_passerby(store: SQLiteDatabase) -> None:
    empty = await _loaded(store)
    assert empty.list_residents() == []
    fallback = empty.pick_one()  # 空池退化为临时路人
    assert fallback.is_temporary is True and fallback.role == PersonaRole.PASSERBY

    await _seed_residents(store)
    pool = await _loaded(store, enable_hater=True)
    for _ in range(5):
        persona = pool.pick_one()
        assert persona.user_id and persona.user_nickname and persona.role in PersonaRole
        before = persona.messages_generated
        pool.record_message(persona)
        assert persona.messages_generated == before + 1
    assert sum(pool.get_stats().values()) == 5


def test_temporary_passersby_are_capped() -> None:
    pool = PersonaPool(sim_repo=None)  # type: ignore[arg-type]  # 路人生成不碰数据库
    first = pool.generate_temporary_passerby()
    assert first.is_temporary is True and first.role == PersonaRole.PASSERBY and first.user_id.startswith("passerby_")
    for _ in range(60):
        pool.generate_temporary_passerby()
    assert len(pool._passersby) <= 50  # type: ignore[attr-defined]


async def test_add_update_delete_write_through_to_database(store: SQLiteDatabase) -> None:
    await _seed_residents(store)
    pool = await _loaded(store)

    assert await pool.add_personas([_persona("resident_new", "新观众")]) == 1
    assert await pool.update_persona("resident_000", {"user_nickname": "改名观众", "fans_medal_level": 30}) is True
    assert await pool.update_persona("nonexistent", {"user_nickname": "x"}) is False
    assert await pool.delete_persona("resident_001") is True
    assert await pool.delete_persona("resident_001") is False

    reloaded = {p.user_id: p for p in (await _loaded(store)).list_residents()}  # 新实例重新加载，确认已落库
    assert "resident_new" in reloaded and "resident_001" not in reloaded
    assert (reloaded["resident_000"].user_nickname, reloaded["resident_000"].fans_medal_level) == ("改名观众", 30)


async def test_add_personas_to_empty_pool_then_reload(store: SQLiteDatabase) -> None:
    pool = await _loaded(store)
    assert await pool.add_personas([_persona("resident_a", "观众甲"), _persona("resident_b", "观众乙")]) == 2
    assert len(pool.list_residents()) == 2 and len((await _loaded(store)).list_residents()) == 2
