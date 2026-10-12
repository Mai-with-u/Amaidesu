"""结构化设计回复保持完整；容量终止和语法修补不能伪装成可执行产物。"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.engine import LLMManager, _normalize_generate_input
from src.modules.llm.payload import GenerateRequest, Message, ToolCall
from tests.modules.llm.clients.test_openai_client import _delta, _FakeStream, _make_client, _stream_chunk, _tool_delta


def sdk_response(arguments: str, finish_reason: str = "tool_calls", content: str = "") -> SimpleNamespace:
    """保留服务端实际结束原因，独立于参数是否恰好能被解析。"""
    call = SimpleNamespace(
        id="design-call", type="function", function=SimpleNamespace(name="validate", arguments=arguments)
    )
    message = SimpleNamespace(content=content, tool_calls=[call], reasoning_content=None)
    return SimpleNamespace(
        model="test-model", usage=None, choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
    )


@pytest.mark.parametrize("raw", ['{"design":{"objects":[{"name":"half"', '{"x":1,"x":2}', '{"x":NaN}', "[]", ""])
async def test_strict_arguments_keep_original_and_do_not_repair(raw: str) -> None:
    """原始无效参数进入错误元数据，不能执行修复器猜出的数据。"""
    client, sdk = _make_client()
    sdk.chat.completions.create.return_value = sdk_response(raw)
    request = GenerateRequest(messages=[Message(role="user", parts=["设计"])], strict_tool_arguments=True)
    with patch("src.modules.llm.clients.openai.arguments.repair_json", side_effect=AssertionError("不能自动修补设计")):
        result = await client.generate(request, model="test-model")
    assert result.tool_calls[0].arguments == {}
    assert result.tool_calls[0].raw_arguments == raw
    assert result.tool_calls[0].arguments_error


async def test_unlimited_request_preserves_full_reply_and_does_not_modify_shared_client() -> None:
    """长设计与普通调用均保留完整输出，不发送旧 provider 的固定额度。"""
    client, sdk = _make_client({"max_tokens": 128})
    full_text = "完整设计说明" * 4000
    sdk.chat.completions.create.return_value = sdk_response('{"design":{}}', content=full_text)
    request = GenerateRequest(
        messages=[Message(role="user", parts=["设计"])],
        strict_tool_arguments=True,
    )
    result = await client.generate(request, model="test-model")
    assert "max_tokens" not in sdk.chat.completions.create.call_args.kwargs
    assert result.content == full_text and result.finish_reason == "tool_calls"
    await client.chat([{"role": "user", "content": "普通请求"}], model="test-model")
    assert "max_tokens" not in sdk.chat.completions.create.call_args.kwargs


async def test_streaming_preserves_length_reason_and_raw_incomplete_arguments() -> None:
    """流式末帧的容量终止必须贯通，不能在拼接后自动补成完整工具参数。"""
    client, sdk = _make_client({"max_tokens": 128})
    raw = '{"design":{"objects":['
    final = _stream_chunk(_delta())
    final.choices[0].finish_reason = "length"
    sdk.chat.completions.create.return_value = _FakeStream(
        [
            _stream_chunk(_delta(tool_calls=[_tool_delta(0, name="validate", call_id="design-call", arguments=raw)])),
            final,
        ]
    )
    result = await client.generate(
        GenerateRequest(strict_tool_arguments=True),
        model="test-model",
        on_delta=lambda kind, text: None,
    )
    assert "max_tokens" not in sdk.chat.completions.create.call_args.kwargs
    assert result.finish_reason == "length"
    assert result.tool_calls[0].raw_arguments == raw and result.tool_calls[0].arguments_error


async def test_engine_preserves_integrity_metadata_and_output_policy() -> None:
    """经过真实引擎的新旧响应转换后，结束原因和原始错误参数仍完整保留。"""
    client, sdk = _make_client()
    sdk.close = AsyncMock()
    raw = '{"design":'
    sdk.chat.completions.create.return_value = sdk_response(raw, "length")
    config: dict[str, Any] = {
        "llm_providers": [
            {"name": "p", "client_type": "openai", "base_url": "https://api.example.com/v1", "api_key": "test"}
        ],
        "llm_models": [{"name": "m", "model_identifier": "test-model", "api_provider": "p"}],
        "llm_profiles": {"minecraft_builder": {"model_list": ["m"], "max_tokens": 8192}},
    }
    manager = LLMManager()
    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=client)}):
        await manager.setup(config)
        try:
            result = await manager.generate("设计", profile="minecraft_builder", strict_tool_arguments=True)
        finally:
            await manager.cleanup()
    assert result.finish_reason == "length"
    assert result.tool_calls[0].raw_arguments == raw and result.tool_calls[0].arguments_error
    assert "max_tokens" not in sdk.chat.completions.create.call_args.kwargs


def test_invalid_previous_arguments_are_sent_back_without_repair() -> None:
    """向模型说明错误时保留它上一次的实际输出，不伪造成空对象。"""
    raw = '{"design":'
    request = _normalize_generate_input(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c", "type": "function", "function": {"name": "validate", "arguments": raw}},
                ],
            },
            {"role": "tool", "tool_call_id": "c", "content": "参数不完整，请重新生成"},
        ],
        system=None,
        tools=None,
        temperature=None,
    )
    client, _ = _make_client()
    messages = client._request_to_openai_messages(request)
    assert messages[0]["tool_calls"][0]["function"]["arguments"] == raw
    assert request.messages[0].tool_calls[0] == ToolCall(id="c", name="validate", arguments={}, raw_arguments=raw)
