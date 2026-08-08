"""测试 GiftGenerator"""

import asyncio

import pytest

from src.modules.simulator.config_schema import (
    SimulatorConfigSchema,
)
from src.modules.simulator.gift_generator import (
    GiftGenerator,
)


@pytest.fixture
def gift_data_dir(tmp_path):
    """创建空数据目录（load 时自动写入默认礼物清单）"""
    data_dir = tmp_path / "data" / "simulator"
    data_dir.mkdir(parents=True)
    return data_dir


@pytest.mark.asyncio
async def test_load_gifts(gift_data_dir):
    cfg = SimulatorConfigSchema()
    gen = GiftGenerator(cfg, data_dir=gift_data_dir)
    await gen.load()
    assert len(gen._gifts) > 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_load_creates_default_gifts_file(gift_data_dir):
    cfg = SimulatorConfigSchema()
    gen = GiftGenerator(cfg, data_dir=gift_data_dir)
    await gen.load()
    assert (gift_data_dir / "gifts.toml").exists()


@pytest.mark.asyncio
async def test_load_keeps_user_edited_gifts(gift_data_dir):
    gifts_path = gift_data_dir / "gifts.toml"
    gifts_path.write_text(
        '[gifts]\n\n[[gifts.items]]\n'
        'gift_id = "custom_gift"\ngift_name = "自定义礼物"\n'
        'category = "normal"\nweight = 10\ndata_type = "gift"\n',
        encoding="utf-8",
    )

    cfg = SimulatorConfigSchema()
    gen = GiftGenerator(cfg, data_dir=gift_data_dir)
    await gen.load()

    assert len(gen._gifts) == 1  # type: ignore[attr-defined]
    assert gen._gifts[0].gift_id == "custom_gift"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_generate_gift(gift_data_dir):
    from src.modules.simulator.types import (
        StreamerContextSnapshot,
    )

    cfg = SimulatorConfigSchema()
    gen = GiftGenerator(cfg, data_dir=gift_data_dir)
    await gen.load()
    ctx = StreamerContextSnapshot()
    msg = await gen.generate_gift(ctx)
    assert msg is not None
    assert msg.data_type == "gift"


@pytest.mark.asyncio
async def test_generate_sc_no_llm(gift_data_dir):
    from src.modules.simulator.types import (
        StreamerContextSnapshot,
    )

    cfg = SimulatorConfigSchema()
    gen = GiftGenerator(cfg, data_dir=gift_data_dir)
    await gen.load()
    ctx = StreamerContextSnapshot()
    msg = await gen.generate_sc(ctx)
    assert msg is not None
    assert msg.data_type == "super_chat"
    assert msg.sc_amount_rmb is not None


@pytest.mark.asyncio
async def test_generate_gift_excludes_sc_category(gift_data_dir):
    from src.modules.simulator.types import (
        StreamerContextSnapshot,
    )

    cfg = SimulatorConfigSchema()
    gen = GiftGenerator(cfg, data_dir=gift_data_dir)
    await gen.load()
    ctx = StreamerContextSnapshot()
    for _ in range(50):
        msg = await gen.generate_gift(ctx)
        assert msg is not None
        assert msg.data_type == "gift"
        assert msg.gift is not None
        assert msg.gift.category != "sc"


@pytest.mark.asyncio
async def test_pick_random_gift_exclude_categories(gift_data_dir):
    cfg = SimulatorConfigSchema()
    gen = GiftGenerator(cfg, data_dir=gift_data_dir)
    await gen.load()
    picked = gen._pick_random_gift(exclude_categories={"sc"})  # type: ignore[attr-defined]
    assert picked is not None
    assert picked.category != "sc"
