"""空响应经真实客户端和引擎传播时，SDK 请求次数必须服从同一重试额度。"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.engine import LLMManager
from tests.modules.llm.clients.test_openai_client import _FakeStream, _make_client, _response
from tests.modules.llm.test_retry_policy import RETRY_LIMIT, _policy_config


async def test_completed_empty_stream_does_not_multiply_engine_retries() -> None:
    """三次重试只发三次请求；失败仍如实上报，不能用空输出推进工具执行。"""
    client, sdk = _make_client()
    sdk.close = AsyncMock()

    async def respond(**arguments: Any) -> Any:
        return _FakeStream([]) if arguments.get("stream") else _response(content="")

    sdk.chat.completions.create.side_effect = respond
    config = _policy_config()
    config["llm_profiles"]["planner"]["model_list"] = ["m1"]
    manager = LLMManager()
    with patch.dict(_CLIENT_DISPATCH, {"policyfake": MagicMock(return_value=client)}):
        await manager.setup(config)
        try:
            result = await manager.generate("继续当前任务", profile="planner", on_delta=lambda kind, text: None)
        finally:
            await manager.cleanup()
    assert not result.success
    assert "响应内容为空" in result.error
    assert sdk.chat.completions.create.await_count == RETRY_LIMIT
    assert all(call.kwargs.get("stream") for call in sdk.chat.completions.create.call_args_list)
