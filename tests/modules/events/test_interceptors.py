"""EventInterceptor / InterceptorChain 与 EventBus 的集成：优先级、丢弃、异常隔离、改写 payload、作用域过滤"""

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import pytest
from pydantic import BaseModel

from src.modules.events.event_bus import EventBus
from src.modules.events.interceptors import (
    EventInterceptor,
    InterceptorChain,
    RateLimitInterceptor,
    SimilarFilterInterceptor,
)


class SimpleTestEvent(BaseModel):
    message: str = "test"


class _Append(EventInterceptor):
    """把 marker 追加到 payload['message'] 末尾；可选声明作用域。"""

    def __init__(self, marker: str, scope: Tuple[str, ...] = ()) -> None:
        self._marker = marker
        self.scope_prefixes = scope

    @property
    def name(self) -> str:
        return f"append_{self._marker}"

    async def intercept(self, event_name: str, payload: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
        payload["message"] += self._marker
        return payload


class _Drop(EventInterceptor):
    @property
    def name(self) -> str:
        return "always_drop"

    async def intercept(self, event_name: str, payload: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
        return None


class _Raise(EventInterceptor):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "raise"

    async def intercept(self, event_name: str, payload: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
        self.calls += 1
        raise RuntimeError("boom")


class _Record(EventInterceptor):
    def __init__(self, name: str, record: List[str], priority: int = 100) -> None:
        self._name = name
        self._record = record
        self.priority = priority

    @property
    def name(self) -> str:
        return self._name

    async def intercept(self, event_name: str, payload: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
        self._record.append(self._name)
        return payload


async def _emit_and_collect(bus: EventBus, message: str = "x") -> list[str]:
    received: list[str] = []

    async def handler(event_name, payload: SimpleTestEvent, source: str) -> None:
        received.append(payload.message)

    bus.on("evt", handler, SimpleTestEvent)
    await bus.emit("evt", SimpleTestEvent(message=message), source="s")
    await asyncio.sleep(0.05)
    return received


async def test_empty_chain_short_circuits_and_register_order_is_priority_stable() -> None:
    chain = InterceptorChain()
    payload = {"x": 1}
    assert not chain and await chain.apply("any", payload, "src") is payload  # 空链原样返回同一对象

    record: List[str] = []
    for name, priority in (("late", 200), ("early", 50), ("mid1", 100), ("mid2", 100)):
        chain.register(_Record(name, record, priority))
    assert chain.get_order() == ["early", "mid1", "mid2", "late"]  # 数值小者先行，同优先级保持注册序
    await chain.apply("evt", {}, "src")
    assert record == ["early", "mid1", "mid2", "late"]

    assert chain.unregister("mid1") is True and chain.unregister("mid1") is False and len(chain) == 3


async def test_chain_drop_stops_later_interceptors_and_exception_is_passed_through() -> None:
    chain = InterceptorChain()
    record: List[str] = []
    chain.register(_Record("before", record, 10))
    chain.register(_Drop())
    chain.register(_Record("after", record, 200))
    assert await chain.apply("evt", {"x": 1}, "src") is None
    assert record == ["before"]  # drop 后链立即终止

    chain = InterceptorChain()
    record.clear()
    raiser = _Raise()
    chain.register(_Record("before", record, 10))
    chain.register(raiser)
    chain.register(_Record("after", record, 200))
    assert await chain.apply("evt", {"x": 1}, "src") == {"x": 1}  # 异常被捕获，视为 pass-through
    assert raiser.calls == 1 and record == ["before", "after"]


async def test_scope_prefixes_filter_events_per_interceptor() -> None:
    chain = InterceptorChain()
    chain.register(_Append("[R]", ("room.message.",)))
    assert await chain.apply("game.milestone", {"message": "x"}, "src") == {"message": "x"}  # 域外跳过
    assert await chain.apply("room.message.danmaku", {"message": "x"}, "src") == {"message": "x[R]"}

    unscoped = InterceptorChain()
    unscoped.register(_Append("[A]"))  # 空作用域 = 对所有事件生效
    for event_name in ("game.error", "room.message.gift", "tool.result.speak"):
        assert await unscoped.apply(event_name, {"message": "x"}, "src") == {"message": "x[A]"}

    # 限流/相似过滤显式声明 room.message.*，防下游事件被误伤
    assert RateLimitInterceptor.scope_prefixes == SimilarFilterInterceptor.scope_prefixes == ("room.message.",)


async def test_bus_applies_interceptors_in_order_and_handler_sees_modified_payload() -> None:
    bus = EventBus(enable_stats=False)
    assert await _emit_and_collect(bus, "plain") == ["plain"]  # 无拦截器：行为不变

    bus = EventBus(enable_stats=False)
    for marker in ("[1]", "[2]", "[3]"):
        bus.add_interceptor(_Append(marker))
    assert await _emit_and_collect(bus) == ["x[1][2][3]"]


async def test_bus_drop_skips_dispatch_and_exception_does_not_break_emit() -> None:
    bus = EventBus(enable_stats=False)
    bus.add_interceptor(_Append("[A]"))
    bus.add_interceptor(_Drop())
    assert await _emit_and_collect(bus) == []  # 先改写再丢弃：handler 不被调用

    bus = EventBus(enable_stats=False)
    bus.add_interceptor(_Append("[A]"))
    bus.add_interceptor(_Raise())
    bus.add_interceptor(_Append("[C]"))
    assert await _emit_and_collect(bus) == ["x[A][C]"]  # 中间拦截器异常被隔离，前后改写都生效


async def test_remove_interceptor_by_name() -> None:
    bus = EventBus(enable_stats=False)
    bus.add_interceptor(_Append("[A]"))
    bus.add_interceptor(_Append("[B]"))
    assert bus.remove_interceptor("append_[A]") is True and bus.remove_interceptor("nonexistent") is False
    assert bus.get_interceptor_names() == ["append_[B]"]
    assert await _emit_and_collect(bus) == ["x[B]"]


def test_interceptor_is_abstract_without_name_or_intercept() -> None:
    class _NoName(EventInterceptor):
        async def intercept(self, event_name, payload, source):
            return payload

    class _NoIntercept(EventInterceptor):
        @property
        def name(self) -> str:
            return "no_intercept"

    for cls in (EventInterceptor, _NoName, _NoIntercept):
        with pytest.raises(TypeError):
            cls()  # type: ignore[abstract]
