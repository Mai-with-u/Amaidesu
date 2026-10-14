"""结构化设计回复保持完整；容量终止和语法修补不能伪装成可执行产物。"""

from unittest.mock import patch

import pytest

from src.modules.llm.engine import _normalize_generate_input
from src.modules.llm.payload import GenerateRequest, Message, ToolCall
from tests.modules.llm.fakes import FakeStream, chunk, engine_with, make_openai_client, sdk_tool_reply, tool_delta


@pytest.mark.parametrize("raw", ['{"design":{"objects":[{"name":"half"', '{"x":1,"x":2}', '{"x":NaN}', "[]", ""])
async def test_strict_arguments_keep_original_and_do_not_repair(raw: str) -> None:
    """原始无效参数进入错误元数据，不能执行修复器猜出的数据。"""
    client, sdk = make_openai_client()
    sdk.chat.completions.create.return_value = sdk_tool_reply(raw)
    request = GenerateRequest(messages=[Message(role="user", parts=["设计"])], strict_tool_arguments=True)
    with patch("src.modules.llm.clients.openai.arguments.repair_json", side_effect=AssertionError("不能自动修补设计")):
        result = await client.generate(request, model="test-model")
    assert result.tool_calls[0].arguments == {}
    assert result.tool_calls[0].raw_arguments == raw
    assert result.tool_calls[0].arguments_error


async def test_unlimited_request_preserves_full_reply_and_does_not_modify_shared_client() -> None:
    """长设计与普通调用均保留完整输出，不发送旧 provider 的固定额度。"""
    client, sdk = make_openai_client({"max_tokens": 128})
    full_text = "完整设计说明" * 4000
    sdk.chat.completions.create.return_value = sdk_tool_reply('{"design":{}}', content=full_text)
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
    client, sdk = make_openai_client({"max_tokens": 128})
    raw = '{"design":{"objects":['
    sdk.chat.completions.create.return_value = FakeStream(
        [
            chunk(tool_calls=[tool_delta(0, name="validate", call_id="design-call", arguments=raw)]),
            chunk(finish_reason="length"),
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
    client, sdk = make_openai_client()
    raw = '{"design":'
    sdk.chat.completions.create.return_value = sdk_tool_reply(raw, "length")
    async with engine_with(client, {"minecraft_builder": {"max_tokens": 8192}}) as manager:
        result = await manager.generate("设计", profile="minecraft_builder", strict_tool_arguments=True)
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
    client, _ = make_openai_client()
    messages = client._request_to_openai_messages(request)
    assert messages[0]["tool_calls"][0]["function"]["arguments"] == raw
    assert request.messages[0].tool_calls[0] == ToolCall(id="c", name="validate", arguments={}, raw_arguments=raw)
