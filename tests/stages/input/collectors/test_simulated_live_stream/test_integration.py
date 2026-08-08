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
    ctx = collector._context_service
    assert ctx is not None
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


@pytest.mark.asyncio
async def test_stop_then_start_clears_stop_event():
    collector = SimulatedLiveStreamCollector(
        config={}, event_bus=MagicMock(),
        llm_service=MagicMock(), prompt_service=MagicMock(),
        context_service=MagicMock(), event_history_service=MagicMock(),
    )
    ctx = collector._context_service
    assert ctx is not None
    ctx.get_history = AsyncMock(return_value=[])
    ctx.list_sessions = AsyncMock(return_value=[])
    await collector.start()
    assert collector.is_started is True
    assert collector._stop_event.is_set() is False  # type: ignore[attr-defined]

    await collector.stop()
    assert collector.is_started is False
    assert collector._stop_event.is_set() is True  # type: ignore[attr-defined]

    await collector.start()
    assert collector.is_started is True
    assert collector._stop_event.is_set() is False  # type: ignore[attr-defined]
    await collector.stop()
    await collector.cleanup()


@pytest.mark.asyncio
async def test_to_normalized_message_preserves_session_id():
    collector = SimulatedLiveStreamCollector(config={}, event_bus=MagicMock())
    msg = GeneratedMessage(
        text="test",
        persona=Persona(user_id="t", user_nickname="t", role=PersonaRole.FAN, personality="p", speaking_style="s"),
        data_type="text",
    )
    nm = collector._to_normalized_message(msg, "session-42")
    assert nm.session_id == "session-42"
    assert nm.room_id == "session-42"


@pytest.mark.asyncio
async def test_generate_message_gift_branch_records_usage():
    from src.stages.input.collectors.simulated_live_stream.types import (
        GiftItem,
        StreamerContextSnapshot,
    )

    collector = SimulatedLiveStreamCollector(config={}, event_bus=MagicMock())
    collector._llm_wrapper = MagicMock()  # type: ignore[attr-defined]
    collector._token_budget = MagicMock()  # type: ignore[attr-defined]
    collector._token_budget.is_budget_exceeded.return_value = False
    collector._cadence = MagicMock()  # type: ignore[attr-defined]
    collector._cadence.get_state.return_value = "NORMAL"
    collector._gift_generator = MagicMock()  # type: ignore[attr-defined]
    gift_msg = GeneratedMessage(
        text="",
        persona=Persona(user_id="t", user_nickname="t", role=PersonaRole.FAN, personality="p", speaking_style="s"),
        data_type="gift",
        gift=GiftItem(gift_id="g1", gift_name="测试", category="normal", weight=1, data_type="gift"),
        tokens_used=88,
    )
    collector._gift_generator.generate_gift = AsyncMock(return_value=gift_msg)
    collector._pick_message_type = lambda: "gift"  # type: ignore[method-assign]

    msg = await collector._generate_message(StreamerContextSnapshot())
    assert msg is not None
    collector._token_budget.record_usage.assert_called_once_with(88)


@pytest.mark.asyncio
async def test_generate_message_sc_branch_records_usage():
    from src.stages.input.collectors.simulated_live_stream.types import (
        GiftItem,
        StreamerContextSnapshot,
    )

    collector = SimulatedLiveStreamCollector(config={}, event_bus=MagicMock())
    collector._llm_wrapper = MagicMock()  # type: ignore[attr-defined]
    collector._token_budget = MagicMock()  # type: ignore[attr-defined]
    collector._token_budget.is_budget_exceeded.return_value = False
    collector._cadence = MagicMock()  # type: ignore[attr-defined]
    collector._cadence.get_state.return_value = "NORMAL"
    collector._gift_generator = MagicMock()  # type: ignore[attr-defined]
    sc_msg = GeneratedMessage(
        text="感谢支持",
        persona=Persona(user_id="t", user_nickname="t", role=PersonaRole.FAN, personality="p", speaking_style="s"),
        data_type="super_chat",
        gift=GiftItem(gift_id="sc1", gift_name="超级火箭", category="sc", weight=1, data_type="super_chat", sc_amount_rmb=100),
        sc_amount_rmb=100,
        tokens_used=77,
    )
    collector._gift_generator.generate_sc = AsyncMock(return_value=sc_msg)
    collector._pick_message_type = lambda: "sc"  # type: ignore[method-assign]

    msg = await collector._generate_message(StreamerContextSnapshot())
    assert msg is not None
    collector._token_budget.record_usage.assert_called_once_with(77)
