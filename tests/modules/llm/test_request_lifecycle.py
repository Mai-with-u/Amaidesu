"""生成完整等待服务端完成，主动取消仍能回收请求与重试任务。"""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, Optional
from unittest.mock import patch

import pytest

from src.modules.llm.client import BaseLLMClient
from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.clients.openai.client import OpenAIClient
from src.modules.llm.engine import LLMManager
from src.modules.llm.errors import LLMInterruptedError, RetryableError
from src.modules.llm.payload import GenerateRequest, Response


async def _manager(behavior: Callable[..., Coroutine[Any, Any, None]]) -> LLMManager:
    """旧配置里的极短时限不能再打断真实引擎调用。"""

    class FakeClient(BaseLLMClient):
        def __init__(self, config: dict[str, Any]) -> None:
            super().__init__(config)
            self.calls: list[str] = []

        async def generate(
            self,
            request: GenerateRequest,
            *,
            model: str,
            temperature: float | None = None,
            reasoning_effort: Optional[str] = None,
            on_delta: Any = None,
            interrupt_flag: Any = None,
        ) -> Response:
            self.calls.append(model)
            await behavior(on_delta)
            return Response(success=True, content="完整生成", model=model)

    manager = LLMManager()
    with patch.dict(_CLIENT_DISPATCH, {"lifecyclefake": FakeClient}):
        await manager.setup(
            {
                "llm_providers": [{"name": "p", "client_type": "lifecyclefake", "max_retries": 2, "retry_delay": 0.02}],
                "llm_models": [
                    {"name": "m1", "model_identifier": "m1", "api_provider": "p"},
                    {"name": "m2", "model_identifier": "m2", "api_provider": "p"},
                ],
                "llm_profiles": {"planner": {"model_list": ["m1", "m2"], "hard_timeout_ms": 1}},
            }
        )
    return manager


@pytest.mark.parametrize("streaming", [False, True])
async def test_generation_waits_for_complete_response(streaming: bool) -> None:
    """等待跨过旧时限后仍在同一模型生成，流式前后两段都能到达。"""
    started, release = asyncio.Event(), asyncio.Event()
    received: list[str] = []

    async def behavior(on_delta: Any) -> None:
        if on_delta:
            on_delta("content", "开头")
        started.set()
        await release.wait()
        if on_delta:
            on_delta("content", "结尾")

    manager = await _manager(behavior)
    task = asyncio.create_task(
        manager.generate(
            "长任务",
            on_delta=(lambda kind, text: received.append(text)) if streaming else None,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.sleep(0.03)
        assert not task.done()
        release.set()
        response = await asyncio.wait_for(task, 1)
        assert response.content == "完整生成"
        assert manager._provider_clients["p"].calls == ["m1"]
        assert received == (["开头", "结尾"] if streaming else [])
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("cancel_parent", [False, True])
async def test_explicit_cancellation_reaps_request(cancel_parent: bool) -> None:
    """停止游戏或取消父任务时立即收回生成，且不会错误地换模型继续。"""
    started, cleaned, interrupt = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def behavior(on_delta: Any) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    manager = await _manager(behavior)
    task = asyncio.create_task(manager.generate("等待", interrupt=interrupt))
    await asyncio.wait_for(started.wait(), 1)
    if cancel_parent:
        task.cancel()
    else:
        interrupt.set()
    with pytest.raises(asyncio.CancelledError if cancel_parent else LLMInterruptedError):
        await asyncio.wait_for(task, 1)
    assert cleaned.is_set()
    assert manager._provider_clients["p"].calls == ["m1"]


async def test_retry_is_not_cut_off_by_old_deadline() -> None:
    """限流后的退避能完成，随后在同一模型成功，不受旧 profile 时限影响。"""
    attempts = 0

    async def behavior(on_delta: Any) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RetryableError("服务端暂时限流")

    manager = await _manager(behavior)
    response = await asyncio.wait_for(manager.generate("重试"), 1)
    assert response.success and attempts == 2
    assert manager._provider_clients["p"].calls == ["m1", "m1"]


def test_sdk_does_not_apply_a_request_deadline() -> None:
    """明确关闭 SDK 自带超时，旧 provider timeout 也不能恢复生成时限。"""
    with patch("src.modules.llm.clients.openai.client.AsyncOpenAI") as sdk:
        OpenAIClient({"api_key": "test", "base_url": "https://example.test/v1", "timeout": 1})
    assert sdk.call_args.kwargs["timeout"] is None
