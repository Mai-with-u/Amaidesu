"""测试 SimulatorLLMWrapper"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.llm.payload import Response, Usage

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
    m.generate = AsyncMock(return_value=Response(success=True, content="主播加油！", usage=Usage(total_tokens=10)))
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
    mock_llm.generate = AsyncMock(return_value=Response(success=False, content="", usage=Usage(total_tokens=0)))
    cfg = SimulatorConfigSchema()
    wrapper = SimulatorLLMWrapper(cfg, mock_llm)
    msg = await wrapper.generate_viewer_message(persona, context)
    assert msg is None


@pytest.mark.asyncio
async def test_empty_response_returns_none(mock_llm, persona, context):
    mock_llm.generate = AsyncMock(return_value=Response(success=True, content="   ", usage=Usage(total_tokens=0)))
    cfg = SimulatorConfigSchema()
    wrapper = SimulatorLLMWrapper(cfg, mock_llm)
    msg = await wrapper.generate_viewer_message(persona, context)
    assert msg is None


@pytest.mark.asyncio
async def test_long_message_is_complete(
    mock_llm: MagicMock, persona: Persona, context: StreamerContextSnapshot
) -> None:
    """模拟观众的完整发言在清洗包装后保留到末尾。"""
    long_text = "a" * 200
    mock_llm.generate = AsyncMock(return_value=Response(success=True, content=long_text, usage=Usage(total_tokens=200)))
    cfg = SimulatorConfigSchema()
    wrapper = SimulatorLLMWrapper(cfg, mock_llm)
    msg = await wrapper.generate_viewer_message(persona, context)
    assert msg is not None
    assert msg.text == long_text


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


# --- generate_personas ---


def _mock_generate(content: str):
    return AsyncMock(return_value=Response(success=True, content=content, usage=Usage(total_tokens=50)))


@pytest.mark.asyncio
async def test_generate_personas(mock_llm):
    mock_llm.generate = _mock_generate(
        '[{"user_nickname": "麦芽糖", "role": "fan", "personality": "热情捧场", '
        '"speaking_style": "爱用感叹号", "fans_medal_level": 10, "guard_level": 0}]'
    )
    cfg = SimulatorConfigSchema()
    wrapper = SimulatorLLMWrapper(cfg, mock_llm)
    personas = await wrapper.generate_personas(count=1)
    assert len(personas) == 1
    assert personas[0].user_nickname == "麦芽糖"
    assert personas[0].role == PersonaRole.FAN
    assert personas[0].user_id.startswith("resident_")


@pytest.mark.asyncio
async def test_generate_personas_markdown_wrapped_json(mock_llm):
    mock_llm.generate = _mock_generate(
        '```json\n[{"user_nickname": "黑粉一号", "role": "hater", "personality": "毒舌", '
        '"speaking_style": "简短", "fans_medal_level": 3, "guard_level": 0}]\n```'
    )
    cfg = SimulatorConfigSchema()
    wrapper = SimulatorLLMWrapper(cfg, mock_llm)
    personas = await wrapper.generate_personas(count=1)
    assert len(personas) == 1
    assert personas[0].role == PersonaRole.HATER


@pytest.mark.asyncio
async def test_generate_personas_invalid_json_returns_empty(mock_llm):
    mock_llm.generate = _mock_generate("这不是 JSON")
    cfg = SimulatorConfigSchema()
    wrapper = SimulatorLLMWrapper(cfg, mock_llm)
    personas = await wrapper.generate_personas(count=3)
    assert personas == []


@pytest.mark.asyncio
async def test_generate_personas_llm_failure_returns_empty(mock_llm):
    mock_llm.generate = AsyncMock(return_value=Response(success=False, content="", usage=Usage(total_tokens=0)))
    cfg = SimulatorConfigSchema()
    wrapper = SimulatorLLMWrapper(cfg, mock_llm)
    personas = await wrapper.generate_personas(count=3)
    assert personas == []


@pytest.mark.asyncio
async def test_generate_personas_no_truncation(mock_llm):
    long_json = (
        "["
        + ",".join(
            '{"user_nickname": "观众%02d", "role": "fan", "personality": "%s", '
            '"speaking_style": "短", "fans_medal_level": 5, "guard_level": 0}' % (i, "p" * 60)
            for i in range(10)
        )
        + "]"
    )
    mock_llm.generate = _mock_generate(long_json)
    cfg = SimulatorConfigSchema()
    wrapper = SimulatorLLMWrapper(cfg, mock_llm)
    personas = await wrapper.generate_personas(count=10)
    assert len(personas) == 10  # 完整解析全部人设


# --- 推理模型空 content 兜底重试 ---


@pytest.mark.asyncio
async def test_empty_content_with_thinking_retries_same_budget(mock_llm, persona, context):
    empty_with_thinking = Response(success=True, content="", reasoning_content="x" * 200, usage=Usage(total_tokens=150))
    ok_response = Response(success=True, content="终于有内容了", usage=Usage(total_tokens=300))
    mock_llm.generate = AsyncMock(side_effect=[empty_with_thinking, ok_response])

    cfg = SimulatorConfigSchema()
    wrapper = SimulatorLLMWrapper(cfg, mock_llm)
    msg = await wrapper.generate_viewer_message(persona, context)

    assert msg is not None
    assert msg.text == "终于有内容了"
    assert mock_llm.generate.await_count == 2
    second_max_tokens = mock_llm.generate.await_args_list[1].kwargs.get("max_tokens")
    assert second_max_tokens is None  # 重试保持相同参数，不提高输出上限（宿主不发送输出额度）


@pytest.mark.asyncio
async def test_empty_content_without_thinking_no_retry(mock_llm, persona, context):
    empty_no_thinking = Response(success=True, content="", reasoning_content=None, usage=Usage(total_tokens=10))
    mock_llm.generate = AsyncMock(return_value=empty_no_thinking)

    cfg = SimulatorConfigSchema()
    wrapper = SimulatorLLMWrapper(cfg, mock_llm)
    msg = await wrapper.generate_viewer_message(persona, context)

    assert msg is None
    assert mock_llm.generate.await_count == 1
