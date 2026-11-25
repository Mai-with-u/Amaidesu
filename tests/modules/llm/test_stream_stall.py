"""流式停滞守卫：超时判定挂死、有输出后重新计时"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.modules.llm.client import BaseLLMClient
from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.engine import LLMManager
from src.modules.llm.errors import LLMTimeoutError
from src.modules.llm.payload import GenerateRequest, Response
from src.modules.llm.stall import DEFAULT_STALL_TIMEOUT_MS, StreamStallGuard
from tests.helpers import FakeClock
from tests.modules.llm.fakes import fake_stall_waiter


def _guard(clock: FakeClock, timeout_ms: int = DEFAULT_STALL_TIMEOUT_MS) -> StreamStallGuard:
    return StreamStallGuard(timeout_ms, clock=clock.now_ms, wait=fake_stall_waiter(clock))


async def test_wait_returns_result_before_stall_limit() -> None:
    """上限内拿到结果就正常返回。"""
    clock = FakeClock()
    guard = _guard(clock)

    async def produce() -> str:
        return "chunk"

    assert await guard.wait_for(produce()) == "chunk"


async def test_wait_raises_timeout_when_nothing_arrives() -> None:
    """一直等不到结果就判定挂死：抛超时异常交回上层。"""
    clock = FakeClock()
    guard = _guard(clock)
    never = asyncio.Event()

    async def produce() -> Any:
        await never.wait()
        return "chunk"

    with pytest.raises(LLMTimeoutError, match="停滞"):
        await guard.wait_for(produce())


async def test_output_resets_the_stall_countdown() -> None:
    """有输出后重新计时：离上次输出的间隔归零，剩余上限恢复。"""
    clock = FakeClock()
    guard = _guard(clock)
    clock.tick(25_000)

    assert guard.remaining_ms() == 5_000
    guard.note_output()
    assert guard.remaining_ms() == DEFAULT_STALL_TIMEOUT_MS
    assert guard.idle_ms() == 0


async def test_on_delta_fires_before_call_completes() -> None:
    """流式增量直通：增量在整次调用结束前即送达调用方，Engine 不缓冲。"""
    received_event = asyncio.Event()
    received: list[str] = []

    class TypewriterFakeClient(BaseLLMClient):
        async def generate(self, request: GenerateRequest, *, model: str, on_delta: Any = None, **kw: Any) -> Response:
            on_delta("content", "第一个增量")
            await asyncio.wait_for(received_event.wait(), timeout=5.0)
            return Response(success=True, content="完整回复", model=model)

    def consumer(kind: str, text: str) -> None:
        del kind
        received.append(text)
        received_event.set()

    config = {
        "llm_providers": [{"name": "fake", "client_type": "typewriter", "max_retries": 0}],
        "llm_models": [{"name": "m1", "model_identifier": "fake-1", "api_provider": "fake"}],
        "llm_profiles": {"planner": {"model_list": ["m1"]}},
    }
    with patch.dict(_CLIENT_DISPATCH, {"typewriter": TypewriterFakeClient}):
        with patch("src.modules.llm.call_observer.record_call_outcome", new_callable=AsyncMock):
            manager = LLMManager(llm_repo=object())
            await manager.setup(config)
            response = await manager.generate("你好", profile="planner", on_delta=consumer)

    assert response.success is True and received == ["第一个增量"]
