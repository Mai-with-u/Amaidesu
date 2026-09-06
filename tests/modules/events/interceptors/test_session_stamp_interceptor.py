"""SessionStampInterceptor 单测：场次归属单点注入。"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from src.modules.events.interceptors.chain import InterceptorChain
from src.modules.events.interceptors.session_stamp import SessionStampInterceptor


class _FakeSessionManager:
    def __init__(self, pk: int = 42, *, raise_on_resolve: bool = False) -> None:
        self._pk = pk
        self._raise = raise_on_resolve
        self.calls = 0

    async def resolve_pk(self) -> int:
        self.calls += 1
        if self._raise:
            raise RuntimeError("解析失败")
        return self._pk


@pytest.mark.asyncio
async def test_stamps_unstamped_payload() -> None:
    ic = SessionStampInterceptor(_FakeSessionManager(42))
    payload: Dict[str, Any] = {"live_session_id": 0, "content": "hi"}
    out = await ic.intercept("room.message.danmaku", payload, "test")
    assert out is not None
    assert out["live_session_id"] == 42


@pytest.mark.asyncio
async def test_skips_when_already_stamped() -> None:
    sm = _FakeSessionManager(42)
    ic = SessionStampInterceptor(sm)
    payload: Dict[str, Any] = {"live_session_id": 7}
    out = await ic.intercept("streamer.speech", payload, "test")
    assert out is not None
    assert out["live_session_id"] == 7
    assert sm.calls == 0


@pytest.mark.asyncio
async def test_skips_payload_without_field() -> None:
    """无 live_session_id 字段的同域事件（如 planner.checkpoint）不被注入未知键。"""
    ic = SessionStampInterceptor(_FakeSessionManager(42))
    payload: Dict[str, Any] = {"timeline_summary": "x"}
    out = await ic.intercept("planner.checkpoint", payload, "test")
    assert out is not None
    assert "live_session_id" not in out


@pytest.mark.asyncio
async def test_resolve_failure_passes_payload_through() -> None:
    ic = SessionStampInterceptor(_FakeSessionManager(raise_on_resolve=True))
    payload: Dict[str, Any] = {"live_session_id": 0}
    out = await ic.intercept("room.message.danmaku", payload, "test")
    assert out is not None
    assert out["live_session_id"] == 0


@pytest.mark.asyncio
async def test_scope_prefixes_filter_via_chain() -> None:
    """作用域外的同名键事件（core.*）不进拦截器。"""
    sm = _FakeSessionManager(42)
    chain = InterceptorChain()
    chain.register(SessionStampInterceptor(sm))

    out = await chain.apply("core.startup", {"live_session_id": 0}, "test")
    assert out == {"live_session_id": 0}
    assert sm.calls == 0

    out = await chain.apply("planner.decision", {"live_session_id": 0}, "test")
    assert out is not None
    assert out["live_session_id"] == 42
