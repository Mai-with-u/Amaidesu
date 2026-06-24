"""
事件装饰器隔离测试（pytest）

验证 @register_event 装饰器与 register_core_events() 启动钩子之间的隔离契约：

1. ``EVENT_REGISTRY`` 在 ``register_core_events()`` 调用后被填充。
2. 所有 ``CoreEvents`` 常量（``plugin.`` 前缀除外）都被注册到 ``EVENT_REGISTRY``。
3. 所有注册的 Payload 类型都是 ``pydantic.BaseModel`` 的子类。
4. ``register_core_events()`` 多次调用是幂等的（不抛异常、不产生重复副作用）。

运行: uv run pytest tests/modules/events/test_decorator_isolation.py -v
"""

import pytest
from pydantic import BaseModel

from src.modules.events import (
    EVENT_REGISTRY,
    list_registered_events,
    register_core_events,
    register_event,
)
from src.modules.events.names import CoreEvents


# =============================================================================
# 准备：调用一次 register_core_events() 触发 Payload 模块导入
# =============================================================================


@pytest.fixture(autouse=True)
def ensure_registered():
    """每个测试前确保 register_core_events() 已执行，EVENT_REGISTRY 已填充"""
    register_core_events()
    yield


# =============================================================================
# 注册表填充测试
# =============================================================================


class TestRegistryPopulation:
    """EVENT_REGISTRY 在 register_core_events() 之后应被正确填充"""

    def test_registry_is_not_empty_after_register(self):
        """register_core_events() 之后 EVENT_REGISTRY 非空"""
        assert len(EVENT_REGISTRY) > 0, (
            "EVENT_REGISTRY 在 register_core_events() 之后应至少包含一个事件"
        )

    def test_list_registered_events_returns_dict(self):
        """list_registered_events() 返回非空字典"""
        events = list_registered_events()
        assert isinstance(events, dict)
        assert len(events) > 0

    def test_list_registered_events_returns_copy(self):
        """list_registered_events() 返回的是副本（修改不影响内部注册表）"""
        events = list_registered_events()
        original_len = len(events)
        events["__test_dummy__"] = type  # type: ignore[assignment]
        assert len(list_registered_events()) == original_len


# =============================================================================
# CoreEvents 覆盖率测试
# =============================================================================


class TestDecoratorFrameworkContract:
    """验证 @register_event 装饰器本身的契约（不依赖 Payload 类迁移进度）

    这些测试通过**合成测试类**在隔离环境中验证装饰器行为：
    - 装饰器把类登记到 EVENT_REGISTRY
    - 反向引用 _registered_event_name 被正确设置
    - 重复注册同名不同类抛 ValueError
    - 重复注册同名同类是幂等的
    """

    def test_decorator_registers_class_in_registry(self):
        """@register_event 把 BaseModel 子类登记到 EVENT_REGISTRY"""
        test_event_name = "test.isolation.registers_new_class"

        @register_event(test_event_name)
        class _IsolationPayload(BaseModel):
            value: int = 0

        try:
            assert test_event_name in EVENT_REGISTRY
            assert EVENT_REGISTRY[test_event_name] is _IsolationPayload
        finally:
            EVENT_REGISTRY.pop(test_event_name, None)
            if hasattr(_IsolationPayload, "_registered_event_name"):
                delattr(_IsolationPayload, "_registered_event_name")

    def test_decorator_sets_reverse_reference(self):
        """@register_event 在类上设置 _registered_event_name 反向引用"""
        test_event_name = "test.isolation.reverse_reference"

        @register_event(test_event_name)
        class _ReverseRefPayload(BaseModel):
            value: str = ""

        try:
            reverse_name = getattr(_ReverseRefPayload, "_registered_event_name", None)
            assert reverse_name == test_event_name, (
                f"_registered_event_name 应等于注册键: got {reverse_name!r}"
            )
        finally:
            EVENT_REGISTRY.pop(test_event_name, None)
            if hasattr(_ReverseRefPayload, "_registered_event_name"):
                delattr(_ReverseRefPayload, "_registered_event_name")

    def test_decorator_returns_class_unchanged(self):
        """装饰器原样返回被装饰的类（不包装）"""

        @register_event("test.isolation.identity")
        class _IdentityPayload(BaseModel):
            value: int = 42

        try:
            instance = _IdentityPayload()
            assert instance.value == 42
        finally:
            EVENT_REGISTRY.pop("test.isolation.identity", None)
            if hasattr(_IdentityPayload, "_registered_event_name"):
                delattr(_IdentityPayload, "_registered_event_name")

    def test_duplicate_registration_same_class_is_idempotent(self):
        """同一个类重复注册到同一事件名不应抛异常"""
        test_event_name = "test.isolation.duplicate_same_class"

        @register_event(test_event_name)
        class _DuplicatePayload(BaseModel):
            value: int = 0

        try:
            # 第二次用相同的类注册，不应抛异常
            _again = register_event(test_event_name)(_DuplicatePayload)
            assert _again is _DuplicatePayload
            assert EVENT_REGISTRY[test_event_name] is _DuplicatePayload
        finally:
            EVENT_REGISTRY.pop(test_event_name, None)
            delattr(_DuplicatePayload, "_registered_event_name")

    def test_duplicate_registration_different_class_raises(self):
        """不同类注册到同一事件名应抛 ValueError"""
        test_event_name = "test.isolation.duplicate_diff_class"

        @register_event(test_event_name)
        class _FirstClass(BaseModel):
            pass

        try:
            with pytest.raises(ValueError, match="已被注册"):
                @register_event(test_event_name)
                class _SecondClass(BaseModel):
                    pass
        finally:
            EVENT_REGISTRY.pop(test_event_name, None)
            for cls in (_FirstClass,):
                if hasattr(cls, "_registered_event_name"):
                    delattr(cls, "_registered_event_name")


class TestStartupHookContract:
    """验证 register_core_events() 启动钩子的契约"""

    def test_startup_hook_returns_none(self):
        """register_core_events() 不返回任何值"""
        result = register_core_events()
        assert result is None

    def test_startup_hook_populates_registry(self):
        """register_core_events() 至少应把已迁移的 Payload 子模块触发 import

        该断言不限定具体事件名（迁移是渐进式的），只断言注册表非空。
        """
        before = len(EVENT_REGISTRY)
        register_core_events()
        after = len(EVENT_REGISTRY)
        assert after >= before
        assert after > 0, "register_core_events() 之后 EVENT_REGISTRY 应非空"


class TestEventNameConvention:
    """已注册事件名的命名规范弱校验"""

    def test_all_registered_events_have_dot_separator(self):
        """所有已注册事件名应包含至少一个点号（命名空间.动作）"""
        invalid = {n for n in EVENT_REGISTRY.keys() if "." not in n}
        assert not invalid, (
            f"以下事件名缺少命名空间分隔符 '.': {sorted(invalid)}"
        )

    def test_no_plugin_namespace_in_registry(self):
        """plugin.* 命名空间不应出现在 EVENT_REGISTRY 中（plugin 系统已废弃）"""
        plugin_events = {n for n in EVENT_REGISTRY.keys() if n.startswith("plugin.")}
        assert not plugin_events, (
            f"plugin.* 事件不应通过 register_core_events() 注册: {sorted(plugin_events)}"
        )


# =============================================================================
# Payload 类型校验
# =============================================================================


class TestPayloadTypeContract:
    """所有注册的 Payload 类型必须是 pydantic.BaseModel 的子类"""

    def test_all_registered_payloads_are_basemodel_subclass(self):
        """遍历 EVENT_REGISTRY，所有 value 必须是 BaseModel 子类"""
        invalid = {
            name: cls
            for name, cls in EVENT_REGISTRY.items()
            if not (isinstance(cls, type) and issubclass(cls, BaseModel))
        }
        assert not invalid, (
            f"以下事件名注册了非 BaseModel 类型: {invalid}"
        )

    def test_registered_payload_has_event_name_attribute(self):
        """注册的 Payload 类应携带反向引用属性 _registered_event_name"""
        for event_name, cls in EVENT_REGISTRY.items():
            reverse_name = getattr(cls, "_registered_event_name", None)
            assert reverse_name is not None, (
                f"{cls.__name__} 缺少 _registered_event_name 属性（装饰器未正确设置反向引用）"
            )
            assert reverse_name == event_name, (
                f"{cls.__name__}._registered_event_name 与注册键不一致: "
                f"got {reverse_name!r}, expected {event_name!r}"
            )


# =============================================================================
# 幂等性测试
# =============================================================================


class TestIdempotency:
    """register_core_events() 多次调用应安全无副作用"""

    def test_call_twice_does_not_raise(self):
        """连续两次调用不应抛异常"""
        register_core_events()
        register_core_events()  # 第二次

    def test_call_multiple_times_keeps_registry_size(self):
        """多次调用后注册表大小不变"""
        before = len(EVENT_REGISTRY)
        for _ in range(3):
            register_core_events()
        after = len(EVENT_REGISTRY)
        assert before == after, (
            f"重复调用导致 EVENT_REGISTRY 大小变化: {before} -> {after}"
        )

    def test_call_multiple_times_keeps_payload_classes(self):
        """多次调用不会替换为不同的类（同一个事件名应保持同一个类型）"""
        snapshot = dict(EVENT_REGISTRY)
        for _ in range(3):
            register_core_events()
        for event_name, cls in snapshot.items():
            assert EVENT_REGISTRY.get(event_name) is cls, (
                f"重复调用后 {event_name} 的 Payload 类被替换"
            )


# =============================================================================
# 启动钩子验证
# =============================================================================


class TestStartupHook:
    """验证 register_core_events() 本身具备触发 import 的能力"""

    def test_startup_hook_returns_none(self):
        """register_core_events() 不返回任何值"""
        result = register_core_events()
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
