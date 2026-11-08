"""流式停滞守卫：超时判定挂死、有输出后重新计时"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.modules.llm.errors import LLMTimeoutError
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
