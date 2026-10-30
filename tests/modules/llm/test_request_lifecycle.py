"""生成完整等待服务端完成，主动取消仍能回收请求与重试任务；设了放弃等待时长的用途到点收回、切下一个模型。"""

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
from tests.config.helpers import wait_until


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


class _ManualTimer:
    """放弃等待的计时替身：记下每次开始计时的时长，测试说"到点"才到点。"""

    def __init__(self) -> None:
        self.started: list[int] = []
        self.fire = asyncio.Event()

    async def __call__(self, ms: int) -> None:
        self.started.append(ms)
        await self.fire.wait()


async def _give_up_manager(hang: set[str], timer: _ManualTimer, *, give_up_ms: int) -> tuple[LLMManager, dict]:
    """两个模型的 replyer：``hang`` 里的模型一直不返回（直到被收回）；返回管理器与调用记录。"""
    record: dict = {"calls": [], "reaped": []}

    class HangingClient(BaseLLMClient):
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
            record["calls"].append(model)
            if model in hang:
                try:
                    await asyncio.Event().wait()
                finally:
                    record["reaped"].append(model)
            return Response(success=True, content=f"{model} 的台词", model=model)

    manager = LLMManager(give_up_timer=timer)
    with patch.dict(_CLIENT_DISPATCH, {"hangingfake": HangingClient}):
        await manager.setup(
            {
                "llm_providers": [{"name": "p", "client_type": "hangingfake", "max_retries": 1, "retry_delay": 0}],
                "llm_models": [
                    {"name": "m1", "model_identifier": "m1", "api_provider": "p"},
                    {"name": "m2", "model_identifier": "m2", "api_provider": "p"},
                ],
                "llm_profiles": {"replyer": {"model_list": ["m1", "m2"], "give_up_ms": give_up_ms}},
            }
        )
    return manager, record


async def test_stuck_model_is_given_up_and_next_model_answers() -> None:
    """直播台词这类晚了就没用的调用：卡住的模型到点就收回请求，切名单里的下一个。"""
    timer = _ManualTimer()
    manager, record = await _give_up_manager({"m1"}, timer, give_up_ms=60_000)
    task = asyncio.create_task(manager.generate("说句话", profile="replyer"))
    await wait_until(lambda: record["calls"] == ["m1"], what="第一个模型开始生成")
    timer.fire.set()
    response = await asyncio.wait_for(task, 1)
    assert response.success and response.content == "m2 的台词"
    assert record["reaped"] == ["m1"]  # 卡住的请求被收回，连接不留着
    assert timer.started == [60_000, 60_000]  # 每个模型各自计时


async def test_all_stuck_models_given_up_returns_failure() -> None:
    timer = _ManualTimer()
    timer.fire.set()
    manager, record = await _give_up_manager({"m1", "m2"}, timer, give_up_ms=60_000)
    response = await asyncio.wait_for(manager.generate("说句话", profile="replyer"), 1)
    assert not response.success and "已放弃" in (response.error or "")
    assert record["reaped"] == ["m1", "m2"]


async def test_zero_give_up_waits_without_a_timer() -> None:
    """默认一直等：不设放弃时长的用途不计时，长回复不会被从中间掐断。"""
    timer = _ManualTimer()
    timer.fire.set()
    manager, record = await _give_up_manager(set(), timer, give_up_ms=0)
    response = await asyncio.wait_for(manager.generate("说句话", profile="replyer"), 1)
    assert response.content == "m1 的台词" and timer.started == []
