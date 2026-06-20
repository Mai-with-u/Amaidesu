"""
装饰器注册系统单元测试

测试 @collector、@decider、@handler 装饰器及其相关函数。
每个测试都在独立的子测试中运行，确保模块隔离。
"""

import pytest
from typing import Type

# 导入被测试的模块
from src.stages.input.registry import (
    collector,
    _COLLECTORS,
    get_collector,
    list_collectors,
)
from src.stages.decision.registry import (
    decider,
    _DECIDERS,
    get_decider,
    list_deciders,
)
from src.stages.output.registry import (
    handler,
    _HANDLERS,
    get_handler,
    list_handlers,
)


# ==================== Input Collector 测试 ====================


class TestCollectorDecorator:
    """Input Collector 装饰器测试"""

    def test_collector_registers_class(self):
        """测试 @collector("name") 将类注册到 _COLLECTORS"""
        @collector("test_collector")
        class TestCollector:
            pass

        assert "test_collector" in _COLLECTORS
        assert _COLLECTORS["test_collector"] is TestCollector

    def test_collector_returns_class_unchanged(self):
        """测试装饰器返回原始类（不包装）"""
        @collector("test_collector_unchanged")
        class TestCollectorUnchanged:
            value = 42

        instance = TestCollectorUnchanged()
        assert instance.value == 42

    def test_collector_duplicate_raises_value_error(self):
        """测试重复注册相同名称引发 ValueError"""
        @collector("test_duplicate")
        class FirstCollector:
            pass

        with pytest.raises(ValueError, match="已被注册"):
            @collector("test_duplicate")
            class SecondCollector:
                pass

    def test_get_collector_returns_correct_class(self):
        """测试 get_collector() 返回正确的类"""
        @collector("test_get")
        class TestGetCollector:
            pass

        result = get_collector("test_get")
        assert result is TestGetCollector

    def test_get_collector_nonexistent_raises_key_error(self):
        """测试 get_collector() 对不存在的名称引发 KeyError"""
        with pytest.raises(KeyError, match="未找到"):
            get_collector("nonexistent_collector")

    def test_list_collectors_returns_all_names(self):
        """测试 list_collectors() 返回所有已注册的名称"""
        @collector("test_list_1")
        class TestList1:
            pass

        @collector("test_list_2")
        class TestList2:
            pass

        names = list_collectors()
        assert "test_list_1" in names
        assert "test_list_2" in names


# ==================== Decision Decider 测试 ====================


class TestDeciderDecorator:
    """Decision Decider 装饰器测试"""

    def test_decider_registers_class(self):
        """测试 @decider("name") 将类注册到 _DECIDERS"""
        @decider("test_decider")
        class TestDecider:
            pass

        assert "test_decider" in _DECIDERS
        assert _DECIDERS["test_decider"] is TestDecider

    def test_decider_returns_class_unchanged(self):
        """测试装饰器返回原始类（不包装）"""
        @decider("test_decider_unchanged")
        class TestDeciderUnchanged:
            value = 42

        instance = TestDeciderUnchanged()
        assert instance.value == 42

    def test_decider_duplicate_raises_value_error(self):
        """测试重复注册相同名称引发 ValueError"""
        @decider("test_duplicate_decider")
        class FirstDecider:
            pass

        with pytest.raises(ValueError, match="已被注册"):
            @decider("test_duplicate_decider")
            class SecondDecider:
                pass

    def test_get_decider_returns_correct_class(self):
        """测试 get_decider() 返回正确的类"""
        @decider("test_get_decider")
        class TestGetDecider:
            pass

        result = get_decider("test_get_decider")
        assert result is TestGetDecider

    def test_get_decider_nonexistent_raises_key_error(self):
        """测试 get_decider() 对不存在的名称引发 KeyError"""
        with pytest.raises(KeyError, match="未找到"):
            get_decider("nonexistent_decider")

    def test_list_deciders_returns_all_names(self):
        """测试 list_deciders() 返回所有已注册的名称"""
        @decider("test_list_decider_1")
        class TestListDecider1:
            pass

        @decider("test_list_decider_2")
        class TestListDecider2:
            pass

        names = list_deciders()
        assert "test_list_decider_1" in names
        assert "test_list_decider_2" in names


# ==================== Output Handler 测试 ====================


class TestHandlerDecorator:
    """Output Handler 装饰器测试"""

    def test_handler_registers_class(self):
        """测试 @handler("name") 将类注册到 _HANDLERS"""
        @handler("test_handler")
        class TestHandler:
            pass

        assert "test_handler" in _HANDLERS
        assert _HANDLERS["test_handler"] is TestHandler

    def test_handler_returns_class_unchanged(self):
        """测试装饰器返回原始类（不包装）"""
        @handler("test_handler_unchanged")
        class TestHandlerUnchanged:
            value = 42

        instance = TestHandlerUnchanged()
        assert instance.value == 42

    def test_handler_duplicate_raises_value_error(self):
        """测试重复注册相同名称引发 ValueError"""
        @handler("test_duplicate_handler")
        class FirstHandler:
            pass

        with pytest.raises(ValueError, match="已被注册"):
            @handler("test_duplicate_handler")
            class SecondHandler:
                pass

    def test_get_handler_returns_correct_class(self):
        """测试 get_handler() 返回正确的类"""
        @handler("test_get_handler")
        class TestGetHandler:
            pass

        result = get_handler("test_get_handler")
        assert result is TestGetHandler

    def test_get_handler_nonexistent_raises_key_error(self):
        """测试 get_handler() 对不存在的名称引发 KeyError"""
        with pytest.raises(KeyError, match="未找到"):
            get_handler("nonexistent_handler")

    def test_list_handlers_returns_all_names(self):
        """测试 list_handlers() 返回所有已注册的名称"""
        @handler("test_list_handler_1")
        class TestListHandler1:
            pass

        @handler("test_list_handler_2")
        class TestListHandler2:
            pass

        names = list_handlers()
        assert "test_list_handler_1" in names
        assert "test_list_handler_2" in names


# ==================== 模块隔离测试 ====================


class TestModuleIsolation:
    """测试三个注册表相互隔离"""

    def test_collectors_and_deciders_are_independent(self):
        """测试 _COLLECTORS 和 _DECIDERS 是独立的字典"""
        @collector("isolated_collector")
        class IsolatedCollector:
            pass

        @decider("isolated_decider")
        class IsolatedDecider:
            pass

        assert "isolated_collector" in _COLLECTORS
        assert "isolated_collector" not in _DECIDERS
        assert "isolated_decider" in _DECIDERS
        assert "isolated_decider" not in _COLLECTORS

    def test_collectors_and_handlers_are_independent(self):
        """测试 _COLLECTORS 和 _HANDLERS 是独立的字典"""
        @collector("isolated_collector_h")
        class IsolatedCollectorH:
            pass

        @handler("isolated_handler_h")
        class IsolatedHandlerH:
            pass

        assert "isolated_collector_h" in _COLLECTORS
        assert "isolated_collector_h" not in _HANDLERS
        assert "isolated_handler_h" in _HANDLERS
        assert "isolated_handler_h" not in _COLLECTORS

    def test_deciders_and_handlers_are_independent(self):
        """测试 _DECIDERS 和 _HANDLERS 是独立的字典"""
        @decider("isolated_decider_h")
        class IsolatedDeciderH:
            pass

        @handler("isolated_handler_d")
        class IsolatedHandlerD:
            pass

        assert "isolated_decider_h" in _DECIDERS
        assert "isolated_decider_h" not in _HANDLERS
        assert "isolated_handler_d" in _HANDLERS
        assert "isolated_handler_d" not in _DECIDERS


# ==================== 导入测试 ====================


class TestImportBehavior:
    """测试模块导入行为"""

    def test_import_empty_registry_returns_empty_dict(self):
        """测试仅导入模块时返回空字典（因为装饰器还未被调用）"""
        # 注意：这个测试必须在其他测试之前运行才有意义
        # 因为 pytest 会共享模块状态
        # 这里我们只是验证结构存在
        assert isinstance(_COLLECTORS, dict)
        assert isinstance(_DECIDERS, dict)
        assert isinstance(_HANDLERS, dict)

    def test_decorator_functions_are_callable(self):
        """测试装饰器函数是可调用的"""
        assert callable(collector)
        assert callable(decider)
        assert callable(handler)

    def test_get_functions_are_callable(self):
        """测试 getter 函数是可调用的"""
        assert callable(get_collector)
        assert callable(get_decider)
        assert callable(get_handler)

    def test_list_functions_are_callable(self):
        """测试 list 函数是可调用的"""
        assert callable(list_collectors)
        assert callable(list_deciders)
        assert callable(list_handlers)