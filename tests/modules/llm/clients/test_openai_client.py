import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modules.llm.clients.openai_client import OpenAIClient


MESSAGES = [{"role": "user", "content": "hello"}]


def _response(
    *,
    content: str = "world",
    model: str = "test-model",
    reasoning_content: str | None = None,
    tool_calls: list[object] | None = None,
    usage: object | None = None,
) -> SimpleNamespace:
    message = SimpleNamespace(
        content=content,
        reasoning_content=reasoning_content,
        tool_calls=tool_calls or [],
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        model=model,
        usage=usage,
    )


def _make_client(config: dict[str, object] | None = None) -> tuple[OpenAIClient, MagicMock]:
    merged_config: dict[str, object] = {
        "api_key": "test-key",
        "base_url": "https://api.example.com/v1",
        "model": "test-model",
        "max_tokens": 256,
        "temperature": 0.3,
    }
    if config:
        merged_config.update(config)

    with patch("src.modules.llm.clients.openai_client.AsyncOpenAI") as openai_class:
        sdk_client = MagicMock()
        sdk_client.chat.completions.create = AsyncMock()
        openai_class.return_value = sdk_client
        client = OpenAIClient(merged_config)
    return client, sdk_client


@pytest.mark.asyncio
async def test_chat_basic():
    client, sdk_client = _make_client()
    sdk_client.chat.completions.create.return_value = _response(
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    )

    result = await client.chat(MESSAGES, temperature=0.7, max_tokens=99)

    assert result.success is True
    assert result.content == "world"
    assert result.model == "test-model"
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    sdk_client.chat.completions.create.assert_awaited_once_with(
        model="test-model",
        messages=MESSAGES,
        temperature=0.7,
        max_tokens=99,
    )


@pytest.mark.asyncio
async def test_chat_timeout():
    client, sdk_client = _make_client({"timeout": 0.1})

    async def slow_create(**_kwargs: object) -> object:
        await asyncio.sleep(10)
        return _response()

    sdk_client.chat.completions.create.side_effect = slow_create

    result = await client.chat(MESSAGES)

    assert result.success is False
    assert result.error is not None
    assert "超时" in result.error


@pytest.mark.asyncio
async def test_chat_interrupt():
    client, sdk_client = _make_client({"timeout": 5})
    interrupt_flag = asyncio.Event()

    async def slow_create(**_kwargs: object) -> object:
        await asyncio.sleep(10)
        return _response()

    sdk_client.chat.completions.create.side_effect = slow_create
    asyncio.get_running_loop().call_later(0.1, interrupt_flag.set)

    result = await client.chat(MESSAGES, interrupt_flag=interrupt_flag)

    assert result.success is False
    assert result.error is not None
    assert "中断" in result.error


@pytest.mark.asyncio
async def test_stream_chat_interrupt():
    client, sdk_client = _make_client()
    stop_event = asyncio.Event()
    stream = MagicMock()
    stream.aclose = AsyncMock()

    async def chunks():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="first"))])
        stop_event.set()
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="second"))])

    stream.__aiter__.side_effect = chunks
    sdk_client.chat.completions.create.return_value = stream

    pieces = [piece async for piece in client.stream_chat(MESSAGES, max_tokens=88, stop_event=stop_event)]

    assert pieces == ["first"]
    assert sdk_client.chat.completions.create.await_args.kwargs["max_tokens"] == 88
    stream.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_vision():
    client, sdk_client = _make_client()
    sdk_client.chat.completions.create.return_value = _response(
        content="an image",
        reasoning_content="visual reasoning",
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=6, total_tokens=26),
    )

    result = await client.vision(MESSAGES, [b"not-a-real-image"])

    assert result.success is True
    assert result.content == "an image"
    assert result.reasoning_content == "visual reasoning"
    request = sdk_client.chat.completions.create.await_args.kwargs
    user_content = request["messages"][-1]["content"]
    assert user_content[0] == {"type": "text", "text": "hello"}
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_tool_calls():
    client, sdk_client = _make_client()
    tool_call = SimpleNamespace(
        id="call-1",
        type="function",
        function=SimpleNamespace(name="weather", arguments='{"city": "Tokyo"}'),
    )
    sdk_client.chat.completions.create.return_value = _response(tool_calls=[tool_call])

    result = await client.chat(MESSAGES, tools=[{"type": "function", "function": {"name": "weather"}}])

    assert result.tool_calls == [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "weather", "arguments": {"city": "Tokyo"}},
        }
    ]
    assert sdk_client.chat.completions.create.await_args.kwargs["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_tool_args_json_repair():
    client, sdk_client = _make_client()
    tool_call = SimpleNamespace(
        id="call-2",
        type="function",
        function=SimpleNamespace(name="weather", arguments='{"city": "Tokyo",}'),
    )
    sdk_client.chat.completions.create.return_value = _response(tool_calls=[tool_call])

    result = await client.chat(MESSAGES)

    assert result.tool_calls is not None
    assert result.tool_calls[0]["function"]["arguments"] == {"city": "Tokyo"}


@pytest.mark.asyncio
async def test_reasoning_native():
    client, sdk_client = _make_client({"reasoning_parse_mode": "native"})
    sdk_client.chat.completions.create.return_value = _response(reasoning_content="思考过程")

    result = await client.chat(MESSAGES)

    assert result.content == "world"
    assert result.reasoning_content == "思考过程"


@pytest.mark.asyncio
async def test_reasoning_think_tag():
    client, sdk_client = _make_client({"reasoning_parse_mode": "think_tag"})
    sdk_client.chat.completions.create.return_value = _response(content="<think>internal</think>hello")

    result = await client.chat(MESSAGES)

    assert result.content == "hello"
    assert result.reasoning_content == "internal"


@pytest.mark.asyncio
async def test_reasoning_none():
    client, sdk_client = _make_client({"reasoning_parse_mode": "none"})
    sdk_client.chat.completions.create.return_value = _response(
        content="<think>internal</think>hello", reasoning_content="native"
    )

    result = await client.chat(MESSAGES)

    assert result.content == "<think>internal</think>hello"
    assert result.reasoning_content is None


def test_auth_bearer():
    with patch("src.modules.llm.clients.openai_client.AsyncOpenAI") as openai_class:
        OpenAIClient({"api_key": "secret", "base_url": "https://api.example.com/v1"})

    openai_class.assert_called_once_with(
        api_key="secret",
        base_url="https://api.example.com/v1",
        default_headers=None,
        default_query=None,
    )


def test_auth_header():
    with patch("src.modules.llm.clients.openai_client.AsyncOpenAI") as openai_class:
        OpenAIClient(
            {
                "api_key": "secret",
                "base_url": "https://api.example.com/v1",
                "auth_type": "header",
                "auth_header_name": "X-API-Key",
                "auth_header_prefix": "",
            }
        )

    openai_class.assert_called_once_with(
        api_key="sk-dummy",
        base_url="https://api.example.com/v1",
        default_headers={"X-API-Key": "secret"},
        default_query=None,
    )


def test_auth_query():
    with patch("src.modules.llm.clients.openai_client.AsyncOpenAI") as openai_class:
        OpenAIClient(
            {
                "api_key": "secret",
                "base_url": "https://api.example.com/v1",
                "auth_type": "query",
                "auth_query_name": "key",
            }
        )

    openai_class.assert_called_once_with(
        api_key="sk-dummy",
        base_url="https://api.example.com/v1",
        default_headers=None,
        default_query={"key": "secret"},
    )


def test_auth_none():
    with patch("src.modules.llm.clients.openai_client.AsyncOpenAI") as openai_class:
        OpenAIClient(
            {
                "api_key": "secret",
                "base_url": "https://api.example.com/v1",
                "auth_type": "none",
            }
        )

    openai_class.assert_called_once_with(
        api_key="sk-dummy",
        base_url="https://api.example.com/v1",
        default_headers=None,
        default_query=None,
    )


def test_base_url_normalization():
    with patch("src.modules.llm.clients.openai_client.AsyncOpenAI") as openai_class:
        client = OpenAIClient({"api_key": "secret", "base_url": "localhost:8080/v1///", "model": "info-model"})

    assert openai_class.call_args.kwargs["base_url"] == "http://localhost:8080/v1"
    assert client.client_type_name() == "openai"
    assert client.get_info() == {
        "name": "OpenAIClient",
        "model": "info-model",
        "base_url": "localhost:8080/v1///",
    }
