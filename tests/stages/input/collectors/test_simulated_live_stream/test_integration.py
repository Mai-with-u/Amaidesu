"""集成测试 - 模拟直播间 Collector"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.stages.input.collectors.simulated_live_stream.collector import (
    SimulatedLiveStreamCollector,
)
from src.stages.input.collectors.simulated_live_stream.types import (
    GeneratedMessage,
    Persona,
    PersonaRole,
)


@pytest.mark.asyncio
async def test_collector_registration():
    from src.stages.input.registry import list_collectors
    assert "simulated_live_stream" in list_collectors()


@pytest.mark.asyncio
async def test_collector_instantiation():
    collector = SimulatedLiveStreamCollector(config={}, event_bus=MagicMock())
    assert collector._cfg.base_rate_per_minute == 6.0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_collector_instantiation_with_full_config():
    config = {"base_rate_per_minute": 12.0, "burst_multiplier": 5.0, "enable_hater": True}
    collector = SimulatedLiveStreamCollector(config=config, event_bus=MagicMock())
    assert collector._cfg.base_rate_per_minute == 12.0  # type: ignore[attr-defined]
    assert collector._cfg.burst_multiplier == 5.0  # type: ignore[attr-defined]
    assert collector._cfg.enable_hater is True  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_collector_start_with_di():
    collector = SimulatedLiveStreamCollector(
        config={}, event_bus=MagicMock(),
        llm_service=MagicMock(), prompt_service=MagicMock(),
        context_service=MagicMock(), event_history_service=MagicMock(),
    )
    ctx = collector._context_service  # type: ignore[union-attr]
    ctx.get_history = AsyncMock(return_value=[])
    ctx.list_sessions = AsyncMock(return_value=[])
    await collector.start()
    assert collector.is_started is True
    assert collector._cadence is not None
    await collector.stop()
    await collector.cleanup()


@pytest.mark.asyncio
async def test_collector_config_validation():
    from pydantic import ValidationError
    from src.stages.input.collectors.simulated_live_stream.config_schema import SimulatorConfigSchema
    with pytest.raises(ValidationError):
        SimulatorConfigSchema(base_rate_per_minute=0.05)


@pytest.mark.asyncio
async def test_source_platform_not_real():
    collector = SimulatedLiveStreamCollector(config={}, event_bus=MagicMock())
    msg = GeneratedMessage(
        text="test",
        persona=Persona(user_id="t", user_nickname="t", role=PersonaRole.FAN, personality="p", speaking_style="s"),
        data_type="text",
    )
    nm = collector._to_normalized_message(msg, "sid")
    assert nm.source == "simulated_live_stream"
    assert nm.platform == "simulated"
