"""OpenAIClient：请求拼装、认证方式、思考解析、流式增量、usage 捕获、音频模态、tool 带图拆分与错误翻译"""

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from openai import APIStatusError

from src.modules.llm.clients.openai.client import OpenAIClient
from src.modules.llm.errors import FatalError, LLMInterruptedError, RetryableError
from src.modules.llm.payload import GenerateRequest, ImagePart, Message, ToolCall, ToolSpec, Usage
from tests.modules.llm.fakes import FakeStream, SDK_PATH, chunk, make_openai_client, sdk_reply, tool_delta, usage


def _request(*parts: Any, **fields: Any) -> GenerateRequest:
    return GenerateRequest(messages=[Message(role="user", parts=list(parts or ["hi"]))], **fields)


def _sent(sdk_client: MagicMock) -> dict:
    return sdk_client.chat.completions.create.await_args.kwargs


class _SdkUsage:
    """模拟 SDK CompletionUsage：属性可访问，model_dump 输出原始 dict。"""

    def __init__(self, data: dict) -> None:
        self._data = data
        for key, value in data.items():
            setattr(self, key, value)

    def model_dump(self) -> dict:
        return self._data


async def test_chat_basic_and_slow_reply_ignores_legacy_timeout() -> None:
    client, sdk = make_openai_client({"timeout": 0.001})
    sdk.chat.completions.create.return_value = sdk_reply(usage=usage())
    result = await client.generate(_request("hello"), model="test-model", temperature=0.7)
    assert (result.content, result.model) == ("world", "test-model")
    assert result.usage == Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    sdk.chat.completions.create.assert_awaited_once_with(
        model="test-model", messages=[{"role": "user", "content": "hello"}], temperature=0.7
    )

    # 较慢的完整回复不会因旧 provider 时限被取消
    async def delayed(**kwargs: Any) -> Any:
        await asyncio.sleep(0.03)
        return sdk_reply()

    sdk.chat.completions.create.side_effect = delayed
    assert (await client.generate(_request("hello"), model="test-model")).content == "world"


async def test_chat_interrupt_aborts_with_classified_error() -> None:
    """主动中断走 guarded_call 的分类异常（Engine 表消费 abort），不再折叠为失败响应。"""
    client, sdk = make_openai_client({"timeout": 5})
    interrupt_flag = asyncio.Event()

    async def slow_create(**_kwargs: object) -> object:
        await asyncio.sleep(10)
        return sdk_reply()

    sdk.chat.completions.create.side_effect = slow_create
    asyncio.get_running_loop().call_later(0.1, interrupt_flag.set)
    with pytest.raises(LLMInterruptedError, match="中断"):
        await client.generate(_request("hello"), model="test-model", interrupt_flag=interrupt_flag)


async def test_vision_builds_image_parts() -> None:
    client, sdk = make_openai_client()
    sdk.chat.completions.create.return_value = sdk_reply(content="an image", reasoning_content="visual reasoning")
    result = await client.generate_vision(_request("hello"), [b"not-a-real-image"], model="test-model")

    assert (result.content, result.reasoning_content) == ("an image", "visual reasoning")
    user_content = _sent(sdk)["messages"][-1]["content"]
    assert user_content[0] == {"type": "text", "text": "hello"}
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize("arguments", ['{"city": "Tokyo"}', '{"city": "Tokyo",}'], ids=["valid", "repaired"])
async def test_tool_calls_parse_arguments(arguments: str) -> None:
    client, sdk = make_openai_client()
    call = SimpleNamespace(id="call-1", type="function", function=SimpleNamespace(name="weather", arguments=arguments))
    sdk.chat.completions.create.return_value = sdk_reply(tool_calls=[call])

    result = await client.generate(_request("hello", tools=[ToolSpec(name="w")]), model="test-model")
    assert result.tool_calls == [ToolCall(id="call-1", name="weather", arguments={"city": "Tokyo"})]
    assert _sent(sdk)["tool_choice"] == "auto"


@pytest.mark.parametrize(
    ("mode", "content", "native", "expected"),
    [
        ("native", "world", "思考过程", ("world", "思考过程")),
        ("think_tag", "<think>internal</think>hello", None, ("hello", "internal")),
        ("none", "<think>internal</think>hello", "native", ("<think>internal</think>hello", None)),
    ],
)
async def test_reasoning_parse_modes(mode: str, content: str, native, expected) -> None:
    client, sdk = make_openai_client({"reasoning_parse_mode": mode})
    sdk.chat.completions.create.return_value = sdk_reply(content=content, reasoning_content=native)
    result = await client.generate(_request("hello"), model="test-model")
    assert (result.content, result.reasoning_content) == expected


@pytest.mark.parametrize(
    ("auth", "api_key", "headers", "query"),
    [
        ({}, "secret", None, None),
        (
            {"auth_type": "header", "auth_header_name": "X-API-Key", "auth_header_prefix": ""},
            "sk-dummy",
            {"X-API-Key": "secret"},
            None,
        ),
        ({"auth_type": "query", "auth_query_name": "key"}, "sk-dummy", None, {"key": "secret"}),
        ({"auth_type": "none"}, "sk-dummy", None, None),
    ],
    ids=["bearer", "header", "query", "none"],
)
def test_auth_modes(auth: dict, api_key: str, headers, query) -> None:
    with patch(SDK_PATH) as openai_class:
        OpenAIClient({"api_key": "secret", "base_url": "https://api.example.com/v1", **auth})
    openai_class.assert_called_once_with(
        api_key=api_key,
        base_url="https://api.example.com/v1",
        default_headers=headers,
        default_query=query,
        timeout=None,
    )


def test_base_url_normalization() -> None:
    with patch(SDK_PATH) as openai_class:
        client = OpenAIClient({"api_key": "secret", "base_url": "localhost:8080/v1///", "model": "info-model"})
    assert openai_class.call_args.kwargs["base_url"] == "http://localhost:8080/v1"
    assert client.get_info() == {"name": "OpenAIClient", "model": "info-model", "base_url": "localhost:8080/v1///"}


# 流式路径（on_delta 非 None）：传输层流式、语义层整段


async def test_streaming_emits_deltas_and_assembles_text() -> None:
    client, sdk = make_openai_client()
    sdk.chat.completions.create.return_value = FakeStream(
        [
            chunk(reasoning="想一"),
            chunk(reasoning="想二"),
            chunk(content="你"),
            chunk(content="好"),
            chunk(usage=usage(7, 3)),
        ]
    )
    received = []
    result = await client.generate(
        _request("hello"), model="test-model", on_delta=lambda kind, text: received.append((kind, text))
    )

    assert (result.content, result.reasoning_content) == ("你好", "想一想二")
    assert result.usage == Usage(prompt_tokens=7, completion_tokens=3, total_tokens=10)
    assert received == [("reasoning", "想一"), ("reasoning", "想二"), ("content", "你"), ("content", "好")]


async def test_streaming_accumulates_tool_call_fragments_without_emitting_them() -> None:
    client, sdk = make_openai_client({"reasoning_parse_mode": "none"})
    sdk.chat.completions.create.return_value = FakeStream(
        [
            chunk(reasoning="决定回应"),
            chunk(tool_calls=[tool_delta(0, call_id="call_1", name="reply")]),
            chunk(tool_calls=[tool_delta(0, arguments='{"speech"')]),
            chunk(tool_calls=[tool_delta(0, arguments=': "大家好"}')]),
            chunk(),
        ]
    )
    received = []
    result = await client.generate(
        _request("hello"), model="test-model", on_delta=lambda kind, text: received.append((kind, text))
    )

    assert received == [("reasoning", "决定回应")]
    assert result.tool_calls == [ToolCall(id="call_1", name="reply", arguments={"speech": "大家好"})]


async def test_streaming_create_error_falls_back_to_non_streaming() -> None:
    """流式 create 失败（如端点不支持 stream_options）→ 降级非流式，不产生增量。"""
    client, sdk = make_openai_client()
    sdk.chat.completions.create.side_effect = [Exception("stream_options is not supported"), sdk_reply(content="fb")]
    received = []
    result = await client.generate(
        _request("hello"), model="test-model", on_delta=lambda kind, text: received.append((kind, text))
    )
    assert (result.content, received, sdk.chat.completions.create.await_count) == ("fb", [], 2)


@pytest.mark.parametrize("finish_reason", ["stop", "length"])
async def test_empty_completed_stream_is_left_to_engine_retry(finish_reason: str) -> None:
    """收流后没有正文或工具调用：交给 Engine 重试，客户端不暗中再发一份请求。"""
    client, sdk = make_openai_client()
    final = chunk(usage=usage(8000, 4096))
    final.choices[0].finish_reason = finish_reason
    stream = FakeStream([chunk(reasoning="考虑当前任务"), final])
    sdk.chat.completions.create.side_effect = [stream, sdk_reply(content="unwanted fallback")]

    with pytest.raises(RetryableError) as error:
        await client.generate(_request("推进任务"), model="test-model", on_delta=lambda kind, text: None)
    assert sdk.chat.completions.create.await_count == 1 and stream.closed
    # 保留失败原因和计费量级，不把模型思考正文写进异常日志
    message = str(error.value)
    assert f"finish_reason={finish_reason}" in message and "completion_tokens=4096" in message
    assert "reasoning_chars=6" in message and "考虑当前任务" not in message


# 中立 payload → OpenAI 协议翻译


async def test_generate_translates_text_parts_and_tool_context() -> None:
    client, sdk = make_openai_client({"reasoning_parse_mode": "none"})
    # 裸字符串 parts 是中立契约的文本简写，与 TextPart 等价
    await client.generate(
        GenerateRequest(messages=[Message(role="user", parts=["你好"])], system="你是助手"), model="m"
    )
    assert _sent(sdk)["messages"] == [{"role": "system", "content": "你是助手"}, {"role": "user", "content": "你好"}]

    request = GenerateRequest(
        messages=[
            Message(
                role="assistant", parts=[""], tool_calls=[ToolCall(id="c1", name="query_memory", arguments={"q": "x"})]
            ),
            Message(role="tool", parts=['{"ok": true}'], tool_call_id="c1"),
        ]
    )
    await client.generate(request, model="m")
    messages = _sent(sdk)["messages"]
    assert messages[0]["tool_calls"] == [
        {"id": "c1", "type": "function", "function": {"name": "query_memory", "arguments": '{"q": "x"}'}}
    ]
    assert messages[1] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'}


@pytest.mark.parametrize(
    ("vendor_fields", "expected"),
    [
        ({"completion_tokens_details": SimpleNamespace(reasoning_tokens=123)}, 123),
        ({"completion_tokens_details": {"reasoning_tokens": 456}}, 456),
        ({"completion_reasoning_tokens": 77}, 77),  # DeepSeek 风格
        ({}, None),  # 未上报则不含该键
    ],
)
def test_extract_usage_reasoning_tokens(vendor_fields: dict, expected) -> None:
    extracted = OpenAIClient._extract_usage(
        SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120, **vendor_fields)
    )
    assert (extracted.reasoning_tokens if extracted else None) == expected


@pytest.mark.parametrize("streaming", [False, True])
async def test_response_carries_raw_usage_json(streaming: bool) -> None:
    client, sdk = make_openai_client()
    usage = _SdkUsage(
        {
            "prompt_tokens": 7,
            "completion_tokens": 3,
            "total_tokens": 10,
            "completion_tokens_details": {"reasoning_tokens": 5},
        }
    )
    if streaming:
        sdk.chat.completions.create.return_value = FakeStream([chunk(content="ok"), chunk(usage=usage)])
    else:
        sdk.chat.completions.create.return_value = sdk_reply(usage=usage)

    on_delta = (lambda kind, text: None) if streaming else None
    result = await client.generate(_request(), model="test-model", on_delta=on_delta)
    assert result.usage.reasoning_tokens == 5
    assert json.loads(result.usage_raw_json)["completion_tokens_details"]["reasoning_tokens"] == 5


@pytest.mark.parametrize("path", ["generate", "stream", "vision"])
@pytest.mark.parametrize(
    ("extra_body", "effort", "expected"),
    [
        (
            {"vendor": {"nested": True}},
            "medium",
            {"reasoning_effort": "medium", "extra_body": {"vendor": {"nested": True}}},
        ),
        (None, None, {}),
        ({}, "", {}),  # 空串与空 dict 都视为未配置
    ],
    ids=["set", "unset", "empty"],
)
async def test_reasoning_effort_and_extra_body_sent_only_when_set(path: str, extra_body, effort, expected) -> None:
    client, sdk = make_openai_client({"extra_body": extra_body} if extra_body is not None else None)
    kwargs: dict[str, Any] = {"model": "test-model"}
    if effort is not None:
        kwargs["reasoning_effort"] = effort
    if path == "stream":
        sdk.chat.completions.create.return_value = FakeStream([chunk(content="ok"), chunk(usage=usage(1, 1))])
        await client.generate(_request(), on_delta=lambda kind, text: None, **kwargs)
    elif path == "vision":
        await client.generate_vision(_request("描述这张图"), [b"img"], **kwargs)
    else:
        await client.generate(_request(), **kwargs)

    sent = _sent(sdk)
    assert {k: sent[k] for k in ("reasoning_effort", "extra_body") if k in sent} == expected


# generate_audio：音频输出模态（omni_tts 引擎消费）


@pytest.mark.parametrize(
    "audio",
    [SimpleNamespace(data="QUJD", transcript="你好世界"), {"data": "QUJD", "transcript": "你好世界"}],
    ids=["object", "dict"],
)
async def test_generate_audio_requests_audio_modality_and_backfills_transcript(audio) -> None:
    client, sdk = make_openai_client()
    sdk.chat.completions.create.return_value = sdk_reply(content=None, model="qwen3-omni")
    sdk.chat.completions.create.return_value.choices[0].message.audio = audio

    result = await client.generate_audio(_request("你好世界"), model="qwen3-omni", voice="Chelsie", audio_format="wav")

    sent = _sent(sdk)
    assert sent["modalities"] == ["text", "audio"] and "stream" not in sent
    assert sent["audio"] == {"voice": "Chelsie", "format": "wav"}
    # 语音文本经 transcript 回填 content
    assert (result.audio_base64, result.audio_format, result.content) == ("QUJD", "wav", "你好世界")


async def test_generate_audio_without_audio_returns_plain_text() -> None:
    """端点不支持音频模态时按普通文本响应返回，不报错。"""
    client, sdk = make_openai_client()
    sdk.chat.completions.create.return_value = sdk_reply(content="plain", model="qwen3-omni")
    result = await client.generate_audio(_request("你好"), model="qwen3-omni", voice="Chelsie")
    assert (result.success, result.audio_base64, result.content) == (True, None, "plain")


# tool 观察带图：OpenAI 协议 tool 角色只收文本，图提升为 user 消息


def _translate(*messages: Message) -> list[dict]:
    client = OpenAIClient({"name": "test", "client_type": "openai", "base_url": "https://api.test.com/v1"})
    return client._request_to_openai_messages(GenerateRequest(messages=list(messages)))


def _image(tag: str) -> ImagePart:
    return ImagePart(image=f"data:image/png;base64,{tag}")


def _assert_lifted_image(message: dict, tag: str) -> None:
    assert message["role"] == "user"
    assert message["content"][0]["type"] == "text" and "[系统附图" in message["content"][0]["text"]
    assert message["content"][1] == {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{tag}"}}


def test_single_tool_image_lifts_to_following_user_message() -> None:
    receipt = '{"ok": true, "image": "[图像 1 KB 已省略]"}'
    messages = _translate(
        Message(role="user", parts=["画面里有什么？"]),
        Message(role="tool", parts=[receipt, _image("AAAA")], tool_call_id="call_1"),
    )
    assert len(messages) == 3
    assert messages[1] == {"role": "tool", "tool_call_id": "call_1", "content": receipt}
    _assert_lifted_image(messages[2], "AAAA")

    # 无图 tool 观察：单条字符串 content；user 带图：保持 parts 列表
    assert _translate(Message(role="tool", parts=['{"ok": true}'], tool_call_id="c"))[0]["content"] == '{"ok": true}'
    (user,) = _translate(Message(role="user", parts=["看这张图", _image("BBBB")]))
    assert user["content"] == [
        {"type": "text", "text": "看这张图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBBB"}},
    ]


def test_tool_images_in_multi_tool_run_land_after_run_in_order() -> None:
    """紧邻插入会打断回执连续性，被严格端点 400 拒收（DeepSeek 实测），故推迟到整段回执之后。"""
    calls = [ToolCall(id="c1", name="shot_a", arguments={}), ToolCall(id="c2", name="shot_b", arguments={})]
    messages = _translate(
        Message(role="assistant", parts=[""], tool_calls=calls),
        Message(role="tool", parts=['{"ok": true}', _image("AAAA")], tool_call_id="c1"),
        Message(role="tool", parts=['{"ok": true}', _image("BBBB")], tool_call_id="c2"),
    )
    assert [m["role"] for m in messages] == ["assistant", "tool", "tool", "user", "user"]
    assert [m["tool_call_id"] for m in messages[1:3]] == ["c1", "c2"]
    _assert_lifted_image(messages[3], "AAAA")
    _assert_lifted_image(messages[4], "BBBB")


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        (400, "insufficient tool messages following tool_calls message", "insufficient tool messages"),
        (422, "x" * 800, "…（截断）"),
    ],
)
def test_translate_status_error_carries_truncated_server_body(status: int, message: str, expected: str) -> None:
    """拒绝原因是定位参数问题的第一手证据；超长响应体截断，不撑爆单行展示。"""
    body = {"error": {"message": message}}
    response = httpx.Response(status, json=body, request=httpx.Request("POST", "https://api.test.com/v1/x"))
    translated = OpenAIClient._translate_sdk_error(
        APIStatusError(f"Error code: {status}", response=response, body=body)
    )

    assert isinstance(translated, FatalError)
    assert f"HTTP {status}" in str(translated) and expected in str(translated)
    assert len(str(translated)) < 600
