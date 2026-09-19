import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modules.llm.clients.openai.client import OpenAIClient
from src.modules.llm.payload import GenerateRequest, Message, ToolCall


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

    with patch("src.modules.llm.clients.openai.client.AsyncOpenAI") as openai_class:
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

    result = await client.chat(MESSAGES, model="test-model", temperature=0.7, max_tokens=99)

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

    result = await client.chat(MESSAGES, model="test-model")

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

    result = await client.chat(MESSAGES, model="test-model", interrupt_flag=interrupt_flag)

    assert result.success is False
    assert result.error is not None
    assert "中断" in result.error


@pytest.mark.asyncio
async def test_vision():
    client, sdk_client = _make_client()
    sdk_client.chat.completions.create.return_value = _response(
        content="an image",
        reasoning_content="visual reasoning",
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=6, total_tokens=26),
    )

    result = await client.vision(MESSAGES, [b"not-a-real-image"], model="test-model")

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

    result = await client.chat(
        MESSAGES, model="test-model", tools=[{"type": "function", "function": {"name": "weather"}}]
    )

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

    result = await client.chat(MESSAGES, model="test-model")
    assert result.tool_calls is not None
    assert result.tool_calls[0]["function"]["arguments"] == {"city": "Tokyo"}


@pytest.mark.asyncio
async def test_reasoning_native():
    client, sdk_client = _make_client({"reasoning_parse_mode": "native"})
    sdk_client.chat.completions.create.return_value = _response(reasoning_content="思考过程")

    result = await client.chat(MESSAGES, model="test-model")
    assert result.content == "world"
    assert result.reasoning_content == "思考过程"


@pytest.mark.asyncio
async def test_reasoning_think_tag():
    client, sdk_client = _make_client({"reasoning_parse_mode": "think_tag"})
    sdk_client.chat.completions.create.return_value = _response(content="<think>internal</think>hello")

    result = await client.chat(MESSAGES, model="test-model")
    assert result.content == "hello"
    assert result.reasoning_content == "internal"


@pytest.mark.asyncio
async def test_reasoning_none():
    client, sdk_client = _make_client({"reasoning_parse_mode": "none"})
    sdk_client.chat.completions.create.return_value = _response(
        content="<think>internal</think>hello", reasoning_content="native"
    )

    result = await client.chat(MESSAGES, model="test-model")
    assert result.content == "<think>internal</think>hello"
    assert result.reasoning_content is None


def test_auth_bearer():
    with patch("src.modules.llm.clients.openai.client.AsyncOpenAI") as openai_class:
        OpenAIClient({"api_key": "secret", "base_url": "https://api.example.com/v1"})

    openai_class.assert_called_once_with(
        api_key="secret",
        base_url="https://api.example.com/v1",
        default_headers=None,
        default_query=None,
    )


def test_auth_header():
    with patch("src.modules.llm.clients.openai.client.AsyncOpenAI") as openai_class:
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
    with patch("src.modules.llm.clients.openai.client.AsyncOpenAI") as openai_class:
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
    with patch("src.modules.llm.clients.openai.client.AsyncOpenAI") as openai_class:
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
    with patch("src.modules.llm.clients.openai.client.AsyncOpenAI") as openai_class:
        client = OpenAIClient({"api_key": "secret", "base_url": "localhost:8080/v1///", "model": "info-model"})

    assert openai_class.call_args.kwargs["base_url"] == "http://localhost:8080/v1"
    assert client.get_info() == {
        "name": "OpenAIClient",
        "model": "info-model",
        "base_url": "localhost:8080/v1///",
    }


# ---------------------------------------------------------------------------
# 流式传输路径（on_delta 非 None）：传输层流式、语义层整段（旁路通道）
# ---------------------------------------------------------------------------


class _FakeStream:
    """async iterator 模拟 SSE 流（含 aclose）。"""

    def __init__(self, chunks):
        self._chunks = chunks
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def aclose(self):
        self.closed = True


def _delta(reasoning=None, content=None, tool_calls=None):
    return SimpleNamespace(reasoning_content=reasoning, content=content, tool_calls=tool_calls)


def _tool_delta(index, name=None, arguments=None, call_id=None):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _stream_chunk(delta=None, usage=None, model="test-model"):
    choices = [SimpleNamespace(delta=delta or SimpleNamespace(), finish_reason=None)]
    return SimpleNamespace(choices=choices, usage=usage, model=model)


@pytest.mark.asyncio
async def test_chat_streaming_emits_reasoning_and_content_deltas():
    """reasoning/content 增量逐帧回调，最终响应组装为整段文本。"""
    client, sdk_client = _make_client()
    chunks = [
        _stream_chunk(_delta(reasoning="想一")),
        _stream_chunk(_delta(reasoning="想二")),
        _stream_chunk(_delta(content="你")),
        _stream_chunk(_delta(content="好")),
        _stream_chunk(
            _delta(),
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10),
        ),
    ]
    sdk_client.chat.completions.create.return_value = _FakeStream(chunks)

    received = []
    result = await client.chat(MESSAGES, model="test-model", on_delta=lambda kind, text: received.append((kind, text)))

    assert result.success is True
    assert result.content == "你好"
    assert result.reasoning_content == "想一想二"
    assert result.usage == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    assert received == [("reasoning", "想一"), ("reasoning", "想二"), ("content", "你"), ("content", "好")]


@pytest.mark.asyncio
async def test_chat_streaming_accumulates_tool_call_fragments():
    """tool call arguments 碎片客户端拼接（不外发），最终完整 JSON 解析。"""
    client, sdk_client = _make_client({"reasoning_parse_mode": "none"})
    chunks = [
        _stream_chunk(_delta(reasoning="决定回应")),
        _stream_chunk(_delta(tool_calls=[_tool_delta(0, call_id="call_1", name="reply")])),
        _stream_chunk(_delta(tool_calls=[_tool_delta(0, arguments='{"speech"')])),
        _stream_chunk(_delta(tool_calls=[_tool_delta(0, arguments=': "大家好"}')])),
        _stream_chunk(_delta()),
    ]
    sdk_client.chat.completions.create.return_value = _FakeStream(chunks)

    received = []
    result = await client.chat(MESSAGES, model="test-model", on_delta=lambda kind, text: received.append((kind, text)))

    assert result.success is True
    assert received == [("reasoning", "决定回应")]
    assert result.tool_calls == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "reply", "arguments": {"speech": "大家好"}},
        }
    ]


@pytest.mark.asyncio
async def test_chat_streaming_falls_back_to_non_streaming_on_create_error():
    """流式 create 失败（如端点不支持 stream_options）→ 降级非流式，返回完整结果。"""
    client, sdk_client = _make_client()
    sdk_client.chat.completions.create.side_effect = [
        Exception("stream_options is not supported"),
        _response(content="fallback"),
    ]

    received = []
    result = await client.chat(MESSAGES, model="test-model", on_delta=lambda kind, text: received.append((kind, text)))

    assert result.success is True
    assert result.content == "fallback"
    assert received == []  # 降级路径不产生增量
    assert sdk_client.chat.completions.create.await_count == 2


# ---------------------------------------------------------------------------
# 中立 payload 路径：Engine 归一化后的 Message → OpenAI 协议翻译
# （legacy chat/vision 直接收 dict，不经过该路径，故单列覆盖）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_payload_path_folds_bare_string_parts():
    """裸字符串 parts（中立契约的文本简写）→ OpenAI 字符串 content。

    回归：Engine 归一化文本消息产出的正是 ``parts=["..."]``，适配端必须与
    ``TextPart`` 等价处理，而不是抛「未知的消息片段类型: str」。
    """
    client, sdk_client = _make_client()
    sdk_client.chat.completions.create.return_value = _response()

    request = GenerateRequest(messages=[Message(role="user", parts=["你好"])], system="你是助手")
    result = await client.generate(request, model="test-model")

    assert result.success is True
    assert sdk_client.chat.completions.create.await_args.kwargs["messages"] == [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "你好"},
    ]


@pytest.mark.asyncio
async def test_generate_payload_path_restores_tool_context():
    """assistant tool_calls 还原协议嵌套形态（arguments 回 JSON 字符串），tool 观察带 tool_call_id。"""
    client, sdk_client = _make_client({"reasoning_parse_mode": "none"})
    sdk_client.chat.completions.create.return_value = _response(content="好的")

    request = GenerateRequest(
        messages=[
            Message(
                role="assistant",
                parts=[""],
                tool_calls=[ToolCall(id="c1", name="query_memory", arguments={"q": "x"})],
            ),
            Message(role="tool", parts=['{"ok": true}'], tool_call_id="c1"),
        ]
    )
    await client.generate(request, model="test-model")

    messages = sdk_client.chat.completions.create.await_args.kwargs["messages"]
    assert messages[0]["tool_calls"] == [
        {"id": "c1", "type": "function", "function": {"name": "query_memory", "arguments": '{"q": "x"}'}}
    ]
    assert messages[1] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'}
