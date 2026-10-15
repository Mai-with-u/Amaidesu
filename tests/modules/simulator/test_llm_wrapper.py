"""SimulatorLLMWrapper：观众发言生成（失败/致命错误/空内容/推理模型重试）、token 预算、常驻人设生成"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.llm.payload import Response, Usage
from src.modules.simulator.config_schema import SimulatorConfigSchema
from src.modules.simulator.llm_wrapper import SimulatorLLMWrapper
from src.modules.simulator.types import Persona, PersonaRole, StreamerContextSnapshot

FATAL = "全部模型失败 ['deepseek-flash']: FatalError: 请求被服务端拒绝（HTTP 402）"
RETRYABLE = "全部模型失败 ['deepseek-flash']: RetryableError: 限流（HTTP 429）"


def _reply(content: str = "主播加油！", tokens: int = 10, **kwargs) -> Response:
    return Response(success=True, content=content, usage=Usage(total_tokens=tokens), **kwargs)


def _failure(error: str = "") -> Response:
    return Response(success=False, content="", error=error or None, usage=Usage(total_tokens=0))


@pytest.fixture
def llm() -> MagicMock:
    mock = MagicMock()
    mock.generate = AsyncMock(return_value=_reply())
    return mock


@pytest.fixture
def persona() -> Persona:
    return Persona(
        user_id="test", user_nickname="测试", role=PersonaRole.FAN, personality="捧场", speaking_style="感叹号"
    )


@pytest.fixture
def context() -> StreamerContextSnapshot:
    return StreamerContextSnapshot(recent_messages=["大家好"], recent_emotion="happy", is_online=True)


def _wrapper(llm: MagicMock, **cfg) -> SimulatorLLMWrapper:
    return SimulatorLLMWrapper(SimulatorConfigSchema(**cfg), llm)


async def test_generate_viewer_message_success_tracks_tokens_and_keeps_long_text(llm, persona, context) -> None:
    wrapper = _wrapper(llm, max_concurrent_llm=2)
    msg = await wrapper.generate_viewer_message(persona, context)
    assert msg is not None and (msg.text, msg.data_type, msg.tokens_used) == ("主播加油！", "text", 10)
    assert wrapper.get_token_usage()["total"] >= 10
    assert wrapper._semaphore._value == 2  # type: ignore[attr-defined]

    long_text = "a" * 200  # 完整发言在清洗包装后保留到末尾
    llm.generate = AsyncMock(return_value=_reply(long_text, 200))
    assert (await wrapper.generate_viewer_message(persona, context)).text == long_text


@pytest.mark.parametrize(
    ("response", "fatal"),
    [
        (_failure(), False),
        (_failure(RETRYABLE), False),  # 临时性失败（限流/网络）不算致命
        (_failure(FATAL), True),  # FatalError 被压平进 error 字符串，要暴露给循环收场
        (_reply("   ", 0), False),  # 空白内容
    ],
    ids=["failed", "retryable", "fatal", "blank"],
)
async def test_unusable_responses_return_none_and_flag_only_fatal_errors(
    llm, persona, context, response, fatal
) -> None:
    llm.generate = AsyncMock(return_value=response)
    wrapper = _wrapper(llm)
    assert await wrapper.generate_viewer_message(persona, context) is None
    assert (wrapper.last_fatal_error is not None) is fatal
    if fatal:
        assert "FatalError" in wrapper.last_fatal_error


async def test_empty_content_with_thinking_retries_once_with_same_params(llm, persona, context) -> None:
    thinking_only = Response(success=True, content="", reasoning_content="x" * 200, usage=Usage(total_tokens=150))
    llm.generate = AsyncMock(side_effect=[thinking_only, _reply("终于有内容了", 300)])
    msg = await _wrapper(llm).generate_viewer_message(persona, context)
    assert msg is not None and msg.text == "终于有内容了" and llm.generate.await_count == 2
    assert llm.generate.await_args_list[1].kwargs.get("max_tokens") is None  # 重试不提高输出上限

    plain_empty = Response(success=True, content="", reasoning_content=None, usage=Usage(total_tokens=10))
    llm.generate = AsyncMock(return_value=plain_empty)
    assert await _wrapper(llm).generate_viewer_message(persona, context) is None
    assert llm.generate.await_count == 1  # 没有思考内容就不重试


async def test_token_budget_per_hour_eventually_blocks_generation(llm, persona, context) -> None:
    wrapper = _wrapper(llm, token_budget_per_hour=1000)
    for _ in range(200):
        if await wrapper.generate_viewer_message(persona, context) is None:
            break
    assert wrapper.is_budget_exceeded()


def _personas_json(count: int, personality: str = "热情捧场", role: str = "fan") -> str:
    items = ",".join(
        '{"user_nickname": "观众%02d", "role": "%s", "personality": "%s", "speaking_style": "短", '
        '"fans_medal_level": 5, "guard_level": 0}' % (i, role, personality)
        for i in range(count)
    )
    return f"[{items}]"


async def test_generate_personas_parses_json_including_markdown_wrapped_and_long_lists(llm) -> None:
    llm.generate = AsyncMock(return_value=_reply(_personas_json(1), 50))
    (persona,) = await _wrapper(llm).generate_personas(count=1)
    assert (persona.user_nickname, persona.role) == ("观众00", PersonaRole.FAN) and persona.user_id.startswith(
        "resident_"
    )

    llm.generate = AsyncMock(return_value=_reply("```json\n" + _personas_json(1, "毒舌", "hater") + "\n```", 50))
    (hater,) = await _wrapper(llm).generate_personas(count=1)
    assert hater.role == PersonaRole.HATER

    llm.generate = AsyncMock(return_value=_reply(_personas_json(10, "p" * 60), 50))
    assert len(await _wrapper(llm).generate_personas(count=10)) == 10  # 不截断，完整解析


@pytest.mark.parametrize("response", [_reply("这不是 JSON", 50), _failure()], ids=["invalid-json", "llm-failure"])
async def test_generate_personas_returns_empty_when_unusable(llm, response) -> None:
    llm.generate = AsyncMock(return_value=response)
    assert await _wrapper(llm).generate_personas(count=3) == []
