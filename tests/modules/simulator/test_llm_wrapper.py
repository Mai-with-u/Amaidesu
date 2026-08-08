"""测试 SimulatorLLMWrapper"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.simulator.config_schema import (
    SimulatorConfigSchema,
)
from src.modules.simulator.llm_wrapper import (
    SimulatorLLMWrapper,
)
from src.modules.simulator.types import (
    Persona,
    PersonaRole,
    StreamerContextSnapshot,
)


@pytest.fixture
def mock_llm():
    m = MagicMock()
    resp = MagicMock()
    resp.success = True
    resp.content = "主播加油！"
    resp.usage = {"total_tokens": 10}
    m.chat = AsyncMock(return_value=resp)
    return m


@pytest.fixture
def persona():
    return Persona(
        user_id="test",
        user_nickname="测试",
        role=PersonaRole.FAN,
        personality="捧场",
        speaking_style="感叹号",
    )


@pytest.fixture
def context():
    return StreamerContextSnapshot(recent_messages=["大家好"], recent_emotion="happy", is_online=True)


@pytest.mark.asyncio
async def test_generate_viewer_message(mock_llm, persona, context):
    cfg = SimulatorConfigSchema()
    wrapper = SimulatorLLMWrapper(cfg, mock_llm)
    msg = await wrapper.generate_viewer_message(persona, context)
    assert msg is not None
    assert msg.text == "主播加油！"
    assert msg.data_type == "text"
    assert msg.tokens_used == 10


@pytest.mark.asyncio
async def test_llm_failure_returns_none(mock_llm, persona, context):
    mock_llm.chat = AsyncMock(return_value=MagicMock(success=False, content="", usage={"total_tokens": 0}))
    cfg = SimulatorConfigSchema()
    wrapper = SimulatorLLMWrapper(cfg, mock_llm)
    msg = await wrapper.generate_viewer_message(persona, context)
    assert msg is None


@pytest.mark.asyncio
async def test_empty_response_returns_none(mock_llm, persona, context):
    resp = MagicMock(success=True, content="   ", usage={"total_tokens": 0})
    mock_llm.chat = AsyncMock(return_value=resp)
    cfg = SimulatorConfigSchema()
    wrapper = SimulatorLLMWrapper(cfg, mock_llm)
    msg = await wrapper.generate_viewer_message(persona, context)
    assert msg is None


@pytest.mark.asyncio
async def test_truncation(mock_llm, persona, context):
    long_text = "a" * 200
    resp = MagicMock(success=True, content=long_text, usage={"total_tokens": 200})
    mock_llm.chat = AsyncMock(return_value=resp)
    cfg = SimulatorConfigSchema(max_message_chars=50)
    wrapper = SimulatorLLMWrapper(cfg, mock_llm)
    msg = await wrapper.generate_viewer_message(persona, context)
    assert msg is not None
    assert len(msg.text) <= 50


@pytest.mark.asyncio
async def test_budget_exceeded(mock_llm, persona, context):
    cfg = SimulatorConfigSchema(token_budget_per_hour=1000)
    wrapper = SimulatorLLMWrapper(cfg, mock_llm)
    # Call until budget exceeded
    for _ in range(200):
        msg = await wrapper.generate_viewer_message(persona, context)
        if msg is None:
            break
    # At some point budget should be exceeded
    assert wrapper.is_budget_exceeded()


@pytest.mark.asyncio
async def test_token_tracking(mock_llm, persona, context):
    cfg = SimulatorConfigSchema()
    wrapper = SimulatorLLMWrapper(cfg, mock_llm)
    await wrapper.generate_viewer_message(persona, context)
    assert wrapper.get_token_usage()["total"] >= 10


@pytest.mark.asyncio
async def test_concurrency_semaphore(mock_llm, persona, context):
    cfg = SimulatorConfigSchema(max_concurrent_llm=2)
    wrapper = SimulatorLLMWrapper(cfg, mock_llm)
    assert wrapper._semaphore._value == 2  # type: ignore[attr-defined]
