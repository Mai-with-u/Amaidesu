"""GiftGenerator（SQLite 持久化后端）：礼物/SC 生成、SC 分类排除、目录增删改写穿数据库"""

import pytest

from src.modules.simulator.config_schema import SimulatorConfigSchema
from src.modules.simulator.gift_generator import GiftGenerator
from src.modules.simulator.seed_data import seed_simulator_data
from src.modules.simulator.types import GiftItem, StreamerContextSnapshot
from src.modules.storage.database import SQLiteDatabase


async def _generator(store: SQLiteDatabase) -> GiftGenerator:
    gen = GiftGenerator(SimulatorConfigSchema(), sim_repo=store.sim)
    await gen.load()
    return gen


@pytest.fixture
async def seeded(store: SQLiteDatabase) -> SQLiteDatabase:
    await seed_simulator_data(store.sim)  # 预置内置默认礼物目录
    return store


async def test_empty_catalog_degrades_to_no_gift(store: SQLiteDatabase) -> None:
    gen = await _generator(store)
    assert gen._gifts == []  # type: ignore[attr-defined]
    assert await gen.generate_gift(StreamerContextSnapshot()) is None


async def test_generate_gift_never_picks_sc_category_and_sc_needs_no_llm(seeded: SQLiteDatabase) -> None:
    gen = await _generator(seeded)
    assert len(gen._gifts) > 0  # type: ignore[attr-defined]
    ctx = StreamerContextSnapshot()
    for _ in range(50):
        msg = await gen.generate_gift(ctx)
        assert msg is not None and msg.data_type == "gift" and msg.gift is not None and msg.gift.category != "sc"

    sc = await gen.generate_sc(ctx)
    assert sc is not None and sc.data_type == "super_chat" and sc.sc_amount_rmb is not None
    picked = gen._pick_random_gift(exclude_categories={"sc"})  # type: ignore[attr-defined]
    assert picked is not None and picked.category != "sc"


async def test_catalog_crud_writes_through_to_database(seeded: SQLiteDatabase) -> None:
    gen = await _generator(seeded)
    before = len(gen.list_gifts())

    assert await gen.add_gift(
        GiftItem(gift_id="custom_1", gift_name="自定义", category="normal", weight=3, data_type="gift")
    )
    duplicate = GiftItem(gift_id="small_heart", gift_name="重复小心心", category="normal", weight=1, data_type="gift")
    assert await gen.add_gift(duplicate) is False  # 重复 ID 拒绝
    assert await gen.update_gift("small_heart", {"weight": 99}) is True
    with pytest.raises(ValueError, match="非法字段"):
        await gen.update_gift("small_heart", {"gift_id": "hack"})  # 主键不可改
    assert await gen.delete_gift("custom_1") is True and await gen.delete_gift("custom_1") is False
    assert len(gen.list_gifts()) == before

    reloaded = {g.gift_id: g for g in (await _generator(seeded)).list_gifts()}  # 新实例重新加载，确认已落库
    assert reloaded["small_heart"].weight == 99 and "custom_1" not in reloaded
