"""显式单次生成参数必须穿过真实引擎和客户端到达 SDK，不被用途默认值覆盖。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.engine import LLMManager
from tests.modules.llm.clients.test_openai_client import _make_client
from tests.modules.llm.test_output_integrity import sdk_response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supplied", "expected_limit", "expected_temperature"),
    [
        ({"max_tokens": 2400, "temperature": 0.0}, 2400, 0.0),
        ({}, 4096, 0.7),
    ],
)
async def test_explicit_parameters_and_profile_defaults_reach_sdk(
    supplied: dict, expected_limit: int, expected_temperature: float
) -> None:
    """调用方的零温度和摘要额度保持原值，未指定时继续采用原 profile 默认值。"""
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
    assert sent["max_tokens"] == expected_limit
    assert sent["temperature"] == expected_temperature
