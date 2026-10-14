"""单次生成参数穿过真实引擎和客户端到达 SDK：请求级覆盖用途默认，未设置的参数不发"""

import pytest

from src.modules.llm.payload import GenerateRequest, Message
from tests.modules.llm.fakes import engine_with, make_openai_client, sdk_tool_reply


def _client_with_text_reply():
    client, sdk = make_openai_client()
    reply = sdk_tool_reply("{}", "stop", "短摘要")
    reply.choices[0].message.tool_calls = []
    sdk.chat.completions.create.return_value = reply
    return client, sdk


@pytest.mark.parametrize(("supplied", "expected_temperature"), [({"temperature": 0.0}, 0.0), ({}, 0.7)])
async def test_temperature_reaches_sdk_without_profile_output_cap(supplied: dict, expected_temperature: float) -> None:
    """零温度穿过引擎与客户端；用途的 max_tokens 不注入普通请求。"""
    client, sdk = _client_with_text_reply()
    async with engine_with(client, {"minecraft": {"max_tokens": 4096, "temperature": 0.7}}) as manager:
        assert (await manager.generate("总结已知事实", profile="minecraft", **supplied)).success
    sent = sdk.chat.completions.create.call_args.kwargs
    assert "max_tokens" not in sent and sent["temperature"] == expected_temperature


@pytest.mark.parametrize(
    ("profile_effort", "request_effort", "expected"),
    [
        ("high", None, "high"),  # request 未给 → profile 兜底
        ("high", "low", "low"),  # request 显式 → 覆盖 profile
        ("", "low", "low"),
        ("", None, None),  # 都未设置 → 不发该键
    ],
)
async def test_reasoning_effort_request_overrides_profile(profile_effort: str, request_effort, expected) -> None:
    client, sdk = _client_with_text_reply()
    profile = {"temperature": 0.7, "reasoning_effort": profile_effort}
    async with engine_with(client, {"minecraft": profile}) as manager:
        request = GenerateRequest(messages=[Message(role="user", parts=["总结"])], reasoning_effort=request_effort)
        await manager._call_with_failover("minecraft", method="generate", request=request)
    assert sdk.chat.completions.create.call_args.kwargs.get("reasoning_effort", None) == expected


@pytest.mark.parametrize("extra_body", [{"vendor_x": {"nested": True}, "flag": "on"}, {}])
async def test_provider_extra_body_reaches_sdk_only_when_set(extra_body: dict) -> None:
    client, sdk = make_openai_client({"extra_body": extra_body})
    async with engine_with(client, {"minecraft": {"temperature": 0.7}}, extra_body=extra_body) as manager:
        request = GenerateRequest(messages=[Message(role="user", parts=["hi"])])
        await manager._call_with_failover("minecraft", method="generate", request=request)
    assert sdk.chat.completions.create.call_args.kwargs.get("extra_body") == (extra_body or None)
