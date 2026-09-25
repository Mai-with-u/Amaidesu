"""显式单次生成参数必须穿过真实引擎和客户端到达 SDK，不被用途默认值覆盖。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.engine import LLMManager
from src.modules.llm.payload import GenerateRequest, Message
from tests.modules.llm.clients.test_openai_client import _make_client
from tests.modules.llm.test_output_integrity import sdk_response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supplied", "expected_temperature"),
    [
        ({"temperature": 0.0}, 0.0),
        ({}, 0.7),
    ],
)
async def test_explicit_parameters_and_profile_defaults_reach_sdk(supplied: dict, expected_temperature: float) -> None:
    """零温度穿过引擎与客户端，普通请求也不会注入旧输出额度。"""
    client, sdk = _make_client()
    sdk.close = AsyncMock()
    reply = sdk_response("{}", "stop", "短摘要")
    reply.choices[0].message.tool_calls = []
    sdk.chat.completions.create.return_value = reply
    manager = LLMManager()
    config = {
        "llm_providers": [
            {"name": "p", "client_type": "openai", "base_url": "https://api.example.com/v1", "api_key": "test"}
        ],
        "llm_models": [{"name": "m", "model_identifier": "test-model", "api_provider": "p"}],
        "llm_profiles": {"minecraft": {"model_list": ["m"], "max_tokens": 4096, "temperature": 0.7}},
    }
    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=client)}):
        await manager.setup(config)
        try:
            result = await manager.generate("总结已知事实", profile="minecraft", **supplied)
        finally:
            await manager.cleanup()
    assert result.success
    sent = sdk.chat.completions.create.call_args.kwargs
    assert "max_tokens" not in sent
    assert sent["temperature"] == expected_temperature


# ---------------------------------------------------------------------------
# 思考强度档位（reasoning_effort）优先级：与 temperature 同构的双路径先例
#   request.reasoning_effort（请求级覆盖） > profile.reasoning_effort（用途兜底） > 不发
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile_reasoning_effort", "request_reasoning_effort", "expected"),
    [
        ("high", None, "high"),  # request 未给 → profile 兜底
        ("high", "low", "low"),  # request 显式 → 覆盖 profile
        ("", "low", "low"),  # profile 空 → request 显式兜底
    ],
)
async def test_reasoning_effort_precedence_request_over_profile(
    profile_reasoning_effort: str,
    request_reasoning_effort: str | None,
    expected: str,
) -> None:
    """请求级 reasoning_effort > profile 默认 reasoning_effort。"""
    client, sdk = _make_client()
    sdk.close = AsyncMock()
    reply = sdk_response("{}", "stop", "短摘要")
    reply.choices[0].message.tool_calls = []
    sdk.chat.completions.create.return_value = reply
    manager = LLMManager()
    config = {
        "llm_providers": [
            {"name": "p", "client_type": "openai", "base_url": "https://api.example.com/v1", "api_key": "test"}
        ],
        "llm_models": [{"name": "m", "model_identifier": "test-model", "api_provider": "p"}],
        "llm_profiles": {
            "minecraft": {
                "model_list": ["m"],
                "temperature": 0.7,
                "reasoning_effort": profile_reasoning_effort,
            }
        },
    }
    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=client)}):
        await manager.setup(config)
        try:
            # 请求级覆盖走 payload 路径（request.reasoning_effort），Engine 归一化后透传
            request = GenerateRequest(
                messages=[Message(role="user", parts=["总结已知事实"])],
                reasoning_effort=request_reasoning_effort,
            )
            await manager._call_with_failover(
                "minecraft",
                method="generate",
                request=request,
            )
        finally:
            await manager.cleanup()
    sent = sdk.chat.completions.create.call_args.kwargs
    assert sent.get("reasoning_effort") == expected


@pytest.mark.asyncio
async def test_reasoning_effort_unset_everywhere_omits_key_from_sdk() -> None:
    """profile 与 request 都未设置 reasoning_effort → 请求参数里完全不出现该键（设了才发）。"""
    client, sdk = _make_client()
    sdk.close = AsyncMock()
    reply = sdk_response("{}", "stop", "短摘要")
    reply.choices[0].message.tool_calls = []
    sdk.chat.completions.create.return_value = reply
    manager = LLMManager()
    config = {
        "llm_providers": [
            {"name": "p", "client_type": "openai", "base_url": "https://api.example.com/v1", "api_key": "test"}
        ],
        "llm_models": [{"name": "m", "model_identifier": "test-model", "api_provider": "p"}],
        "llm_profiles": {"minecraft": {"model_list": ["m"], "temperature": 0.7}},  # 无 reasoning_effort
    }
    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=client)}):
        await manager.setup(config)
        try:
            request = GenerateRequest(messages=[Message(role="user", parts=["hi"])])
            await manager._call_with_failover("minecraft", method="generate", request=request)
        finally:
            await manager.cleanup()
    sent = sdk.chat.completions.create.call_args.kwargs
    assert "reasoning_effort" not in sent


@pytest.mark.asyncio
async def test_extra_body_provider_default_reaches_sdk_when_set() -> None:
    """provider 级 extra_body 非空 → 请求参数里带 extra_body（OpenAI SDK 合并进 JSON body）。"""
    client, sdk = _make_client({"extra_body": {"vendor_x": {"nested": True}, "flag": "on"}})
    sdk.close = AsyncMock()
    reply = sdk_response("{}", "stop", "短摘要")
    reply.choices[0].message.tool_calls = []
    sdk.chat.completions.create.return_value = reply
    manager = LLMManager()
    config = {
        "llm_providers": [
            {
                "name": "p",
                "client_type": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "test",
                "extra_body": {"vendor_x": {"nested": True}, "flag": "on"},
            }
        ],
        "llm_models": [{"name": "m", "model_identifier": "test-model", "api_provider": "p"}],
        "llm_profiles": {"minecraft": {"model_list": ["m"], "temperature": 0.7}},
    }
    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=client)}):
        await manager.setup(config)
        try:
            request = GenerateRequest(messages=[Message(role="user", parts=["hi"])])
            await manager._call_with_failover("minecraft", method="generate", request=request)
        finally:
            await manager.cleanup()
    sent = sdk.chat.completions.create.call_args.kwargs
    assert sent["extra_body"] == {"vendor_x": {"nested": True}, "flag": "on"}


@pytest.mark.asyncio
async def test_extra_body_provider_default_omitted_when_empty() -> None:
    """provider 级 extra_body 空 dict → 请求参数里不出现 extra_body 键。"""
    client, sdk = _make_client()
    sdk.close = AsyncMock()
    reply = sdk_response("{}", "stop", "短摘要")
    reply.choices[0].message.tool_calls = []
    sdk.chat.completions.create.return_value = reply
    manager = LLMManager()
    config = {
        "llm_providers": [
            {"name": "p", "client_type": "openai", "base_url": "https://api.example.com/v1", "api_key": "test"}
        ],
        "llm_models": [{"name": "m", "model_identifier": "test-model", "api_provider": "p"}],
        "llm_profiles": {"minecraft": {"model_list": ["m"], "temperature": 0.7}},
    }
    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=client)}):
        await manager.setup(config)
        try:
            request = GenerateRequest(messages=[Message(role="user", parts=["hi"])])
            await manager._call_with_failover("minecraft", method="generate", request=request)
        finally:
            await manager.cleanup()
    sent = sdk.chat.completions.create.call_args.kwargs
    assert "extra_body" not in sent
