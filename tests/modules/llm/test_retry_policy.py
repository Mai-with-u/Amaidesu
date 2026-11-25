"""重试决策分类表：Retryable 同模型有上限重试；Fatal / Timeout 直接切下一个模型；Interrupted 整体中止并上抛"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from src.modules.llm.client import BaseLLMClient
from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.engine import LLMManager
from src.modules.llm.errors import FatalError, LLMInterruptedError, LLMTimeoutError, RetryableError
from src.modules.llm.payload import GenerateRequest, Response
from tests.modules.llm.fakes import FakeStream, make_openai_client, sdk_reply

# provider 显式 max_retries：Retryable 分类下每模型的调用上限
RETRY_LIMIT = 3


def _policy_config() -> Dict[str, Any]:
    """两个模型挂同一 provider（零退避），sequential 起跑。"""
    return {
        "llm_providers": [
            {"name": "fake", "client_type": "policyfake", "max_retries": RETRY_LIMIT, "retry_delay": 0},
        ],
        "llm_models": [
            {"name": "m1", "model_identifier": "fake-1", "api_provider": "fake"},
            {"name": "m2", "model_identifier": "fake-2", "api_provider": "fake"},
        ],
        "llm_profiles": {"planner": {"model_list": ["m1", "m2"], "selection_strategy": {"name": "sequential"}}},
    }


def _policy_client(behavior: Callable[[str], Optional[Exception]]) -> type:
    """按 model 决定失败形态的替身客户端：behavior 返回异常即抛出，None 表示成功。"""

    class PolicyFakeClient(BaseLLMClient):
        def __init__(self, config: Dict[str, Any]) -> None:
            super().__init__(config)
            self.calls: List[str] = []

        async def generate(self, request: GenerateRequest, *, model: str, **kwargs: Any) -> Response:
            self.calls.append(model)
            exc = behavior(model)
            if exc is not None:
                raise exc
            return Response(success=True, content=f"{model} 接管", model=f"{model}-actual")

    return PolicyFakeClient


@pytest.mark.parametrize(
    ("behavior", "expected_calls", "expected_content"),
    [
        (lambda m: FatalError("请求被服务端拒绝（HTTP 400）"), ["fake-1", "fake-2"], None),
        (lambda m: RetryableError("请求过于频繁（429）"), ["fake-1"] * RETRY_LIMIT + ["fake-2"] * RETRY_LIMIT, None),
        (lambda m: LLMTimeoutError("LLM 请求超时") if m == "fake-1" else None, ["fake-1", "fake-2"], "fake-2 接管"),
    ],
    ids=["fatal", "retryable", "timeout"],
)
async def test_error_class_drives_retry_and_failover(behavior, expected_calls: list, expected_content) -> None:
    with patch.dict(_CLIENT_DISPATCH, {"policyfake": _policy_client(behavior)}):
        manager = LLMManager()
        await manager.setup(_policy_config())
        response = await manager.generate("你好", profile="planner")

    assert manager._provider_clients["fake"].calls == expected_calls
    if expected_content is None:
        assert response.success is False and "全部模型失败" in response.error
    else:
        assert response.content == expected_content


async def test_interrupted_aborts_whole_call() -> None:
    with patch.dict(_CLIENT_DISPATCH, {"policyfake": _policy_client(lambda m: LLMInterruptedError("调用被中断"))}):
        manager = LLMManager()
        await manager.setup(_policy_config())
        with pytest.raises(LLMInterruptedError):
            await manager.generate("你好", profile="planner")
    assert manager._provider_clients["fake"].calls == ["fake-1"]


async def test_completed_empty_stream_does_not_multiply_engine_retries() -> None:
    """空响应经真实客户端和引擎传播时，三次重试只发三次请求且不伪造成功输出。"""
    client, sdk = make_openai_client()

    async def respond(**arguments: Any) -> Any:
        return FakeStream([]) if arguments.get("stream") else sdk_reply(content="")

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
    assert not result.success and "响应内容为空" in result.error
    assert sdk.chat.completions.create.await_count == RETRY_LIMIT
    assert all(call.kwargs.get("stream") for call in sdk.chat.completions.create.call_args_list)
