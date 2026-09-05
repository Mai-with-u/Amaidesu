"""测试 GiftGenerator（SQLite 持久化后端）"""

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.simulator.config_schema import (
    SimulatorConfigSchema,
)
from src.modules.simulator.gift_generator import (
    GiftGenerator,
)
from src.modules.simulator.seed_data import seed_simulator_data
from src.modules.simulator.types import (
    GiftItem,
    StreamerContextSnapshot,
)
from src.modules.storage import SQLiteStore


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="gift-gen-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteStore, None]:
    s = SQLiteStore(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
async def seeded_store(store: SQLiteStore) -> SQLiteStore:
    """预置内置默认礼物目录"""
    await seed_simulator_data(store)
    return store


def _new_gen(store: SQLiteStore) -> GiftGenerator:
    return GiftGenerator(SimulatorConfigSchema(), sqlite_store=store)


@pytest.mark.asyncio
async def test_load_gifts(seeded_store):
    gen = _new_gen(seeded_store)
    await gen.load()
    assert len(gen._gifts) > 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_load_empty_catalog(store):
    """空目录 load 后礼物列表为空（生成降级返回 None）"""
    gen = _new_gen(store)
    await gen.load()
    assert gen._gifts == []  # type: ignore[attr-defined]
    assert await gen.generate_gift(StreamerContextSnapshot()) is None


@pytest.mark.asyncio
async def test_generate_gift(seeded_store):
    gen = _new_gen(seeded_store)
    await gen.load()
    ctx = StreamerContextSnapshot()
    msg = await gen.generate_gift(ctx)
    assert msg is not None
    assert msg.data_type == "gift"


@pytest.mark.asyncio
async def test_generate_sc_no_llm(seeded_store):
    gen = _new_gen(seeded_store)
    await gen.load()
    ctx = StreamerContextSnapshot()
    msg = await gen.generate_sc(ctx)
    assert msg is not None
    assert msg.data_type == "super_chat"
    assert msg.sc_amount_rmb is not None


@pytest.mark.asyncio
async def test_generate_gift_excludes_sc_category(seeded_store):
    gen = _new_gen(seeded_store)
    await gen.load()
    ctx = StreamerContextSnapshot()
    for _ in range(50):
        msg = await gen.generate_gift(ctx)
        assert msg is not None
        assert msg.data_type == "gift"
        assert msg.gift is not None
        assert msg.gift.category != "sc"


@pytest.mark.asyncio
async def test_pick_random_gift_exclude_categories(seeded_store):
    gen = _new_gen(seeded_store)
    await gen.load()
    picked = gen._pick_random_gift(exclude_categories={"sc"})  # type: ignore[attr-defined]
    assert picked is not None
    assert picked.category != "sc"


# --- 礼物目录 CRUD（写穿 DB） ---


@pytest.mark.asyncio
async def test_add_gift_persists(seeded_store):
    gen = _new_gen(seeded_store)
    await gen.load()
    added = await gen.add_gift(
        GiftItem(gift_id="custom_1", gift_name="自定义礼物", category="normal", weight=3, data_type="gift")
    )
    assert added is True

    gen2 = _new_gen(seeded_store)
    await gen2.load()
    ids = {g.gift_id for g in gen2.list_gifts()}
    assert "custom_1" in ids


@pytest.mark.asyncio
async def test_add_gift_duplicate_rejected(seeded_store):
    gen = _new_gen(seeded_store)
    await gen.load()
    added = await gen.add_gift(
        GiftItem(gift_id="small_heart", gift_name="重复小心心", category="normal", weight=1, data_type="gift")
    )
    assert added is False


@pytest.mark.asyncio
async def test_update_gift_persists(seeded_store):
    gen = _new_gen(seeded_store)
    await gen.load()
    assert await gen.update_gift("small_heart", {"weight": 99}) is True

    gen2 = _new_gen(seeded_store)
    await gen2.load()
    updated = next(g for g in gen2.list_gifts() if g.gift_id == "small_heart")
    assert updated.weight == 99


@pytest.mark.asyncio
async def test_update_gift_invalid_field_rejected(seeded_store):
    gen = _new_gen(seeded_store)
    await gen.load()
    with pytest.raises(ValueError, match="非法字段"):
        await gen.update_gift("small_heart", {"gift_id": "hack"})


@pytest.mark.asyncio
async def test_delete_gift_persists(seeded_store):
    gen = _new_gen(seeded_store)
    await gen.load()
    before = len(gen.list_gifts())
    assert await gen.delete_gift("small_heart") is True
    assert await gen.delete_gift("small_heart") is False
    assert len(gen.list_gifts()) == before - 1

    gen2 = _new_gen(seeded_store)
    await gen2.load()
    assert all(g.gift_id != "small_heart" for g in gen2.list_gifts())
