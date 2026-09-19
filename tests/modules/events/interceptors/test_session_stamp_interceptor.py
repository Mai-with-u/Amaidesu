"""SessionStampInterceptor 单测：场次归属单点注入。"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from src.modules.events.interceptors.chain import InterceptorChain
from src.modules.events.interceptors.session_stamp import SessionStampInterceptor
from src.modules.events.payloads.rundown import RundownChangedPayload


class _FakeSessionManager:
    def __init__(
        self,
        pk: Optional[int] = 42,
        *,
        raise_on_resolve: bool = False,
    ) -> None:
        self._pk = pk
        self._raise = raise_on_resolve
        self.calls = 0

    async def resolve_pk(self) -> Optional[int]:
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
async def test_stamps_game_events() -> None:
    """game.* 域事件同样盖章：发布方不填的场次主键由拦截器注入 int 值。"""
    ic = SessionStampInterceptor(_FakeSessionManager(42))
    payload: Dict[str, Any] = {"live_session_id": 0, "message": "挖到钻石了！"}
    out = await ic.intercept("game.milestone", payload, "test")
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
    """无 live_session_id 字段的 payload 不被注入未知键（域前缀 + 字段双重过滤）。"""
    ic = SessionStampInterceptor(_FakeSessionManager(42))
    payload: Dict[str, Any] = {"timeline_summary": "x"}
    out = await ic.intercept("planner.decision", payload, "test")
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
async def test_passthrough_when_resolve_returns_none() -> None:
    """``resolve_pk()`` 返回 None 时 payload 原样放行（live_session_id 保持 0），由落库路径跳过。"""
    ic = SessionStampInterceptor(_FakeSessionManager(pk=None))
    payload: Dict[str, Any] = {"live_session_id": 0, "content": "无场次"}
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


@pytest.mark.asyncio
async def test_stamps_rundown_changed_via_chain() -> None:
    """rundown.changed 域在盖章作用域内：流程单变更可归属到单场回看时间线。"""
    sm = _FakeSessionManager(42)
    chain = InterceptorChain()
    chain.register(SessionStampInterceptor(sm))

    payload = RundownChangedPayload(rundown_id="default_first_stream").model_dump()
    assert payload["live_session_id"] == 0

    out = await chain.apply("rundown.changed", payload, "RundownState")
    assert out is not None
    assert out["live_session_id"] == 42
    assert sm.calls == 1
