"""
EventBus 事件拦截器测试

测试 ``EventInterceptor`` + ``InterceptorChain`` 与 EventBus 的集成行为：
- 链按注册顺序顺序执行
- 拦截器返回 ``None`` 丢弃事件（handler 不被调用）
- 拦截器抛异常被捕获 + 日志 + 视为 pass-through（不丢事件，不影响后续）
- EventBus 默认（无拦截器）行为与旧版字节级一致
- EventBus 挂载丢弃拦截器时跳过 handler 分发
- 拦截器可修改 payload，handler 接收修改后的数据

运行: uv run pytest tests/modules/events/test_interceptors.py -v
"""

from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel, Field

from src.modules.events.event_bus import EventBus
from src.modules.events.interceptors import (
    EventInterceptor,
    InterceptorChain,
)


# =============================================================================
# 测试数据模型
# =============================================================================


class SimpleTestEvent(BaseModel):
    """简单测试事件 Model"""

    message: str = Field(default="test", description="测试消息")
    value: int = Field(default=0, description="数值")


# =============================================================================
# 测试用拦截器
# =============================================================================


class _AppendInterceptor(EventInterceptor):
    """把 ``marker`` 串追加到 payload['message'] 末尾"""

    def __init__(self, marker: str) -> None:
        self._marker = marker

    @property
    def name(self) -> str:
        return f"append_{self._marker}"

    async def intercept(
        self,
        event_name: str,
        payload: Dict[str, Any],
        source: str,
    ) -> Optional[Dict[str, Any]]:
        if "message" in payload and isinstance(payload["message"], str):
            payload["message"] = payload["message"] + self._marker
        return payload


class _DropInterceptor(EventInterceptor):
    """总是丢弃事件（返回 None）"""

    @property
    def name(self) -> str:
        return "always_drop"

    async def intercept(
        self,
        event_name: str,
        payload: Dict[str, Any],
        source: str,
    ) -> Optional[Dict[str, Any]]:
        return None


class _RaiseInterceptor(EventInterceptor):
    """intercept() 总是抛异常——必须被链捕获 + 视为 pass-through"""

    def __init__(self, exc: Optional[Exception] = None) -> None:
        self._exc = exc or RuntimeError("boom")
        self._call_count = 0

    @property
    def name(self) -> str:
        return "raise"

    async def intercept(
        self,
        event_name: str,
        payload: Dict[str, Any],
        source: str,
    ) -> Optional[Dict[str, Any]]:
        self._call_count += 1
        raise self._exc


class _RecordCallInterceptor(EventInterceptor):
    """记录每个事件被传给它的次数，便于断言调用顺序"""

    def __init__(self, name: str, record: List[str]) -> None:
        self._name = name
        self._record = record

    @property
    def name(self) -> str:
        return self._name

    async def intercept(
        self,
        event_name: str,
        payload: Dict[str, Any],
        source: str,
    ) -> Optional[Dict[str, Any]]:
        self._record.append(self._name)
        return payload


# =============================================================================
# InterceptorChain 单元测试
# =============================================================================


class TestInterceptorChainBasics:
    """InterceptorChain 自身的纯行为（不挂 EventBus）"""

    @pytest.mark.asyncio
    async def test_empty_chain_returns_payload_unchanged(self):
        """空链 → 直接返回入参 payload"""
        chain = InterceptorChain()
        assert len(chain) == 0
        assert not chain  # __bool__ == False

        payload = {"x": 1}
        result = await chain.apply("any.event", payload, "src")
        assert result is payload  # 同一对象引用（空链短路）

    @pytest.mark.asyncio
    async def test_register_appends_in_order(self):
        """register 按调用顺序追加"""
        chain = InterceptorChain()
        record: List[str] = []

        chain.register(_RecordCallInterceptor("a", record))
        chain.register(_RecordCallInterceptor("b", record))

        assert len(chain) == 2

        await chain.apply("evt", {}, "src")
        assert record == ["a", "b"]

    @pytest.mark.asyncio
    async def test_chain_executes_in_registration_order(self):
        """链内拦截器按注册顺序依次执行"""
        chain = InterceptorChain()
        record: List[str] = []

        chain.register(_RecordCallInterceptor("first", record))
        chain.register(_RecordCallInterceptor("second", record))
        chain.register(_RecordCallInterceptor("third", record))

        await chain.apply("evt", {}, "src")
        assert record == ["first", "second", "third"]

    def test_unregister_removes_by_name(self):
        """按 name 移除首个匹配"""
        chain = InterceptorChain()
        record: List[str] = []

        chain.register(_RecordCallInterceptor("a", record))
        chain.register(_RecordCallInterceptor("b", record))
        assert len(chain) == 2

        removed = chain.unregister("a")
        assert removed is True
        assert len(chain) == 1

        # 重复 unregister 同名返回 False
        assert chain.unregister("a") is False

    @pytest.mark.asyncio
    async def test_first_none_return_drops_immediately(self):
        """第一个返回 None 的拦截器立即终止链，返回 None"""
        chain = InterceptorChain()
        record: List[str] = []

        chain.register(_RecordCallInterceptor("before", record))
        chain.register(_DropInterceptor())
        chain.register(_RecordCallInterceptor("after", record))

        result = await chain.apply("evt", {"x": 1}, "src")
        assert result is None
        # "before" 调用了；"after" 因 drop 终止**不**调用
        assert record == ["before"]

    @pytest.mark.asyncio
    async def test_exception_in_interceptor_is_caught_and_passed_through(self):
        """拦截器抛异常被捕获 + 视为 pass-through（不影响后续/handler）"""
        chain = InterceptorChain()
        record: List[str] = []

        raiser = _RaiseInterceptor()
        chain.register(_RecordCallInterceptor("before", record))
        chain.register(raiser)
        chain.register(_RecordCallInterceptor("after", record))

        # 异常**不**传播；链继续
        result = await chain.apply("evt", {"x": 1}, "src")
        assert result == {"x": 1}  # pass-through：payload 原样返回
        assert raiser._call_count == 1
        # "before"/"after" 都应被调用（异常隔离）
        assert record == ["before", "after"]

    @pytest.mark.asyncio
    async def test_interceptor_can_modify_payload(self):
        """拦截器可原地修改 payload，下一个拦截器/最终结果看到修改"""
        chain = InterceptorChain()
        chain.register(_AppendInterceptor("[A]"))
        chain.register(_AppendInterceptor("[B]"))

        result = await chain.apply("evt", {"message": "hello"}, "src")
        # 两个拦截器都追加 → 顺序应用
        assert result == {"message": "hello[A][B]"}


# =============================================================================
# EventBus 集成测试
# =============================================================================


class TestEventBusInterceptorIntegration:
    """EventBus.add_interceptor + emit 集成行为"""

    @pytest.fixture
    def event_bus(self) -> EventBus:
        return EventBus(enable_stats=False)

    @pytest.mark.asyncio
    async def test_no_interceptors_means_default_behavior_unchanged(self, event_bus: EventBus):
        """无拦截器时，emit 行为与未启用拦截器时字节级一致"""
        received = []

        async def handler(event_name, payload: SimpleTestEvent, source: str):
            received.append((event_name, payload.message, source))

        event_bus.on("test.event", handler, SimpleTestEvent)
        await event_bus.emit("test.event", SimpleTestEvent(message="hello"), source="src", wait=True)

        assert len(received) == 1
        assert received[0] == ("test.event", "hello", "src")

    @pytest.mark.asyncio
    async def test_interceptor_modifies_payload_handler_sees_change(self, event_bus: EventBus):
        """拦截器修改 payload → handler 接收修改后的数据"""
        event_bus.add_interceptor(_AppendInterceptor("!"))

        received = []

        async def handler(event_name, payload: SimpleTestEvent, source: str):
            received.append(payload.message)

        event_bus.on("test.event", handler, SimpleTestEvent)
        await event_bus.emit("test.event", SimpleTestEvent(message="hi"), source="src", wait=True)

        assert received == ["hi!"]

    @pytest.mark.asyncio
    async def test_dropping_interceptor_skips_handler_dispatch(self, event_bus: EventBus):
        """拦截器返回 None → 事件被丢弃，handler **不**被调用"""
        event_bus.add_interceptor(_DropInterceptor())

        received = []

        async def handler(event_name, payload: SimpleTestEvent, source: str):
            received.append("called")

        event_bus.on("test.event", handler, SimpleTestEvent)
        await event_bus.emit("test.event", SimpleTestEvent(message="x"), source="src", wait=True)

        assert received == []

    @pytest.mark.asyncio
    async def test_chained_interceptors_execute_in_registration_order(self, event_bus: EventBus):
        """多个拦截器按注册顺序应用，handler 看到累积修改"""
        event_bus.add_interceptor(_AppendInterceptor("[1]"))
        event_bus.add_interceptor(_AppendInterceptor("[2]"))
        event_bus.add_interceptor(_AppendInterceptor("[3]"))

        received = []

        async def handler(event_name, payload: SimpleTestEvent, source: str):
            received.append(payload.message)

        event_bus.on("evt", handler, SimpleTestEvent)
        await event_bus.emit("evt", SimpleTestEvent(message="x"), source="s", wait=True)

        assert received == ["x[1][2][3]"]

    @pytest.mark.asyncio
    async def test_interceptor_exception_does_not_drop_or_break(self, event_bus: EventBus):
        """拦截器异常被隔离：handler 仍被调用，后续拦截器继续执行"""
        event_bus.add_interceptor(_AppendInterceptor("[A]"))
        event_bus.add_interceptor(_RaiseInterceptor())
        event_bus.add_interceptor(_AppendInterceptor("[C]"))

        received = []

        async def handler(event_name, payload: SimpleTestEvent, source: str):
            received.append(payload.message)

        event_bus.on("evt", handler, SimpleTestEvent)
        await event_bus.emit("evt", SimpleTestEvent(message="x"), source="s", wait=True)

        # [A] 应用 → message="x[A]"
        # Raise 抛异常 → 被捕获视为 pass-through → message 仍为 "x[A]"
        # [C] 继续应用 → message="x[A][C]"
        assert received == ["x[A][C]"]

    @pytest.mark.asyncio
    async def test_remove_interceptor_by_name(self, event_bus: EventBus):
        """remove_interceptor(name) 按名移除"""
        event_bus.add_interceptor(_AppendInterceptor("[A]"))
        event_bus.add_interceptor(_AppendInterceptor("[B]"))

        assert len(event_bus.get_interceptor_names()) == 2

        # 移除 [A]
        removed = event_bus.remove_interceptor("append_[A]")
        assert removed is True
        assert len(event_bus.get_interceptor_names()) == 1
        assert event_bus.get_interceptor_names() == ["append_[B]"]

        # 再次移除不存在的 name → False
        assert event_bus.remove_interceptor("nonexistent") is False

        # emit 时只 [B] 应用
        received = []

        async def handler(event_name, payload: SimpleTestEvent, source: str):
            received.append(payload.message)

        event_bus.on("evt", handler, SimpleTestEvent)
        await event_bus.emit("evt", SimpleTestEvent(message="x"), source="s", wait=True)

        assert received == ["x[B]"]

    @pytest.mark.asyncio
    async def test_interceptor_only_called_for_emitted_event(self, event_bus: EventBus):
        """未 emit 的事件不触发拦截器（仅 emit 时调用 apply）"""
        record: List[str] = []
        interceptor = _RecordCallInterceptor("track", record)
        event_bus.add_interceptor(interceptor)

        # emit 一个无关事件
        async def handler(event_name, payload: SimpleTestEvent, source: str):
            pass

        event_bus.on("evt", handler, SimpleTestEvent)
        await event_bus.emit("evt", SimpleTestEvent(), source="s", wait=True)

        assert record == ["track"]

    @pytest.mark.asyncio
    async def test_drop_after_modify_does_not_dispatch(self, event_bus: EventBus):
        """拦截器先修改再 drop → handler 不被调用（修改也无意义）"""
        event_bus.add_interceptor(_AppendInterceptor("[A]"))
        event_bus.add_interceptor(_DropInterceptor())

        received = []

        async def handler(event_name, payload: SimpleTestEvent, source: str):
            received.append("called")

        event_bus.on("evt", handler, SimpleTestEvent)
        await event_bus.emit("evt", SimpleTestEvent(message="x"), source="s", wait=True)

        assert received == []


# =============================================================================
# EventInterceptor 抽象类契约
# =============================================================================


class TestEventInterceptorContract:
    """EventInterceptor 抽象基类的接口契约"""

    def test_cannot_instantiate_abstract_class(self):
        """EventInterceptor 是抽象类，不能直接实例化"""
        with pytest.raises(TypeError):
            EventInterceptor()  # type: ignore[abstract]

    def test_subclass_missing_name_is_abstract(self):
        """子类若未实现 ``name`` 属性，仍为抽象类（不可实例化）"""

        class _NoName(EventInterceptor):
            async def intercept(self, event_name, payload, source):
                return payload

        # _NoName 继承但未覆盖 name 的 @property 装饰器，故仍是抽象类
        with pytest.raises(TypeError):
            _NoName()  # type: ignore[abstract]

    def test_subclass_missing_intercept_is_abstract(self):
        """子类若未实现 ``intercept`` 方法，仍为抽象类"""

        class _NoIntercept(EventInterceptor):
            @property
            def name(self) -> str:
                return "no_intercept"

        with pytest.raises(TypeError):
            _NoIntercept()  # type: ignore[abstract]


# =============================================================================
# 运行入口
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
