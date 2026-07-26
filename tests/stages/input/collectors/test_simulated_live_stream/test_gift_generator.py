"""测试 GiftGenerator"""

import asyncio

import pytest

from src.stages.input.collectors.simulated_live_stream.config_schema import (
    SimulatorConfigSchema,
)
from src.stages.input.collectors.simulated_live_stream.gift_generator import (
    GiftGenerator,
)


@pytest.mark.asyncio
async def test_load_gifts():
    cfg = SimulatorConfigSchema()
    gen = GiftGenerator(cfg)
    await gen.load()
    assert len(gen._gifts) > 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_generate_gift():
    from src.stages.input.collectors.simulated_live_stream.types import (
        StreamerContextSnapshot,
    )

    cfg = SimulatorConfigSchema()
    gen = GiftGenerator(cfg)
    await gen.load()
    ctx = StreamerContextSnapshot()
    msg = await gen.generate_gift(ctx)
    assert msg is not None
    assert msg.data_type == "gift"


@pytest.mark.asyncio
async def test_generate_sc_no_llm():
    from src.stages.input.collectors.simulated_live_stream.types import (
        StreamerContextSnapshot,
    )

    cfg = SimulatorConfigSchema()
    gen = GiftGenerator(cfg)
    await gen.load()
    ctx = StreamerContextSnapshot()
    msg = await gen.generate_sc(ctx)
    assert msg is not None
    assert msg.data_type == "super_chat"
    assert msg.sc_amount_rmb is not None
