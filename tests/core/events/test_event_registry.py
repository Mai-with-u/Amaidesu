"""
测试 EventRegistry（pytest）

运行: uv run pytest tests/core/events/test_event_registry.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from pydantic import BaseModel
from typing import Optional

from src.core.events.registry import EventRegistry


# =============================================================================
# 测试数据模型
# =============================================================================

class CoreEventData(BaseModel):
    """核心事件测试数据"""
    message: str
    timestamp: float


class PluginEventData(BaseModel):
    """插件事件测试数据"""
    plugin_name: str
    action: str
    value: Optional[int] = None


# =============================================================================
# Fixture
# =============================================================================

@pytest.fixture(autouse=True)
def reset_registry():
    """每个测试前后重置 EventRegistry 状态"""
    # 测试前清理
    EventRegistry.clear_all()

    yield

    # 测试后清理
    EventRegistry.clear_all()


# =============================================================================
# 核心事件注册测试
# =============================================================================

def test_register_core_event_valid():
    """测试注册有效的核心事件"""
    EventRegistry.register_core_event("perception.raw_data.generated", CoreEventData)

    assert EventRegistry.is_registered("perception.raw_data.generated")
    assert EventRegistry.is_core_event("perception.raw_data.generated")
    assert EventRegistry.get("perception.raw_data.generated") == CoreEventData


def test_register_core_event_multiple_valid_prefixes():
    """测试注册所有有效前缀的核心事件"""
    valid_events = [
        ("perception.raw_data.generated", CoreEventData),
        ("normalization.message_ready", CoreEventData),
        ("decision.intent_generated", CoreEventData),
        ("understanding.context_updated", CoreEventData),
        ("expression.parameters_generated", CoreEventData),
        ("render.frame_ready", CoreEventData),
        ("core.system_started", CoreEventData),
    ]

    for event_name, model in valid_events:
        EventRegistry.register_core_event(event_name, model)

    # 验证所有事件都已注册
    for event_name, _ in valid_events:
        assert EventRegistry.is_registered(event_name)
        assert EventRegistry.is_core_event(event_name)


def test_register_core_event_invalid_prefix():
    """测试注册无效前缀的核心事件（应失败）"""
    with pytest.raises(ValueError, match="核心事件名必须以"):
        EventRegistry.register_core_event("invalid.event.name", CoreEventData)

    with pytest.raises(ValueError, match="核心事件名必须以"):
        EventRegistry.register_core_event("plugin.test.event", CoreEventData)

    with pytest.raises(ValueError, match="核心事件名必须以"):
        EventRegistry.register_core_event("custom.event", CoreEventData)


def test_register_core_event_duplicate():
    """测试重复注册核心事件（应覆盖并警告）"""
    EventRegistry.register_core_event("perception.test_event", CoreEventData)

    # 重复注册应该覆盖（记录警告但不抛出异常）
    EventRegistry.register_core_event("perception.test_event", PluginEventData)

    # 验证已覆盖
    assert EventRegistry.get("perception.test_event") == PluginEventData


# =============================================================================
# 插件事件注册测试
# =============================================================================

def test_register_plugin_event_valid():
    """测试注册有效的插件事件"""
    EventRegistry.register_plugin_event("plugin.test.event_triggered", PluginEventData)

    assert EventRegistry.is_registered("plugin.test.event_triggered")
    assert EventRegistry.is_plugin_event("plugin.test.event_triggered")
    assert EventRegistry.get("plugin.test.event_triggered") == PluginEventData


def test_register_plugin_event_invalid_prefix():
    """测试注册无效前缀的插件事件（应失败）"""
    with pytest.raises(ValueError, match="插件事件名必须以 'plugin\\.' 开头"):
        EventRegistry.register_plugin_event("custom.plugin.event", PluginEventData)

    with pytest.raises(ValueError, match="插件事件名必须以 'plugin\\.' 开头"):
        EventRegistry.register_plugin_event("perception.raw_data.generated", PluginEventData)


def test_register_plugin_event_invalid_format():
    """测试注册格式错误的插件事件（应失败）"""
    # 只有前缀，缺少插件名和事件名
    with pytest.raises(ValueError, match="插件事件名格式错误"):
        EventRegistry.register_plugin_event("plugin.", PluginEventData)

    # 只有插件名，缺少事件名
    with pytest.raises(ValueError, match="插件事件名格式错误"):
        EventRegistry.register_plugin_event("plugin.test", PluginEventData)


def test_register_plugin_event_multiple_plugins():
    """测试注册多个插件的事件"""
    events = [
        ("plugin.plugin_a.event1", PluginEventData),
        ("plugin.plugin_a.event2", CoreEventData),
        ("plugin.plugin_b.event1", PluginEventData),
        ("plugin.plugin_b.event2", CoreEventData),
    ]

    for event_name, model in events:
        EventRegistry.register_plugin_event(event_name, model)

    # 验证所有事件都已注册
    for event_name, model in events:
        assert EventRegistry.is_registered(event_name)
        assert EventRegistry.is_plugin_event(event_name)
        assert EventRegistry.get(event_name) == model


def test_register_plugin_event_duplicate():
    """测试重复注册插件事件（应覆盖并警告）"""
    EventRegistry.register_plugin_event("plugin.test.event", PluginEventData)

    # 重复注册应该覆盖（记录警告但不抛出异常）
    EventRegistry.register_plugin_event("plugin.test.event", CoreEventData)

    # 验证已覆盖
    assert EventRegistry.get("plugin.test.event") == CoreEventData


# =============================================================================
# 事件查询测试
# =============================================================================

def test_get_event():
    """测试获取已注册的事件"""
    EventRegistry.register_core_event("core.event1", CoreEventData)
    EventRegistry.register_plugin_event("plugin.test.event1", PluginEventData)

    # 获取核心事件
    assert EventRegistry.get("core.event1") == CoreEventData

    # 获取插件事件
    assert EventRegistry.get("plugin.test.event1") == PluginEventData

    # 获取不存在的事件
    assert EventRegistry.get("nonexistent.event") is None


def test_get_event_core_vs_plugin_separate_namespaces():
    """测试核心事件和插件事件使用独立的命名空间"""
    # 核心事件和插件事件有不同的命名前缀，不会冲突
    EventRegistry.register_core_event("core.test_event", CoreEventData)
    EventRegistry.register_plugin_event("plugin.test.test_event", PluginEventData)

    # 获取核心事件
    assert EventRegistry.get("core.test_event") == CoreEventData

    # 获取插件事件
    assert EventRegistry.get("plugin.test.test_event") == PluginEventData

    # 它们不会互相干扰
    assert EventRegistry.is_core_event("core.test_event") is True
    assert EventRegistry.is_plugin_event("core.test_event") is False

    assert EventRegistry.is_plugin_event("plugin.test.test_event") is True
    assert EventRegistry.is_core_event("plugin.test.test_event") is False


def test_is_registered():
    """测试检查事件是否已注册"""
    EventRegistry.register_core_event("core.registered", CoreEventData)
    EventRegistry.register_plugin_event("plugin.test.registered", PluginEventData)

    # 已注册的核心事件
    assert EventRegistry.is_registered("core.registered") is True

    # 已注册的插件事件
    assert EventRegistry.is_registered("plugin.test.registered") is True

    # 未注册的事件
    assert EventRegistry.is_registered("core.unregistered") is False
    assert EventRegistry.is_registered("plugin.test.unregistered") is False


def test_is_core_event():
    """测试检查是否为核心事件"""
    EventRegistry.register_core_event("core.test_event", CoreEventData)
    EventRegistry.register_plugin_event("plugin.test.event", PluginEventData)

    # 核心事件
    assert EventRegistry.is_core_event("core.test_event") is True

    # 插件事件不是核心事件
    assert EventRegistry.is_core_event("plugin.test.event") is False

    # 未注册的事件不是核心事件
    assert EventRegistry.is_core_event("unregistered.event") is False


def test_is_plugin_event():
    """测试检查是否为插件事件"""
    EventRegistry.register_core_event("core.test_event", CoreEventData)
    EventRegistry.register_plugin_event("plugin.test.event", PluginEventData)

    # 插件事件
    assert EventRegistry.is_plugin_event("plugin.test.event") is True

    # 核心事件不是插件事件
    assert EventRegistry.is_plugin_event("core.test_event") is False

    # 未注册的事件不是插件事件
    assert EventRegistry.is_plugin_event("unregistered.event") is False


# =============================================================================
# 事件列表测试
# =============================================================================

def test_list_core_events():
    """测试列出所有核心事件"""
    EventRegistry.register_core_event("core.event1", CoreEventData)
    EventRegistry.register_core_event("core.event2", PluginEventData)
    EventRegistry.register_plugin_event("plugin.test.event1", PluginEventData)

    core_events = EventRegistry.list_core_events()

    # 应该只包含核心事件
    assert len(core_events) == 2
    assert "core.event1" in core_events
    assert "core.event2" in core_events
    assert "plugin.test.event1" not in core_events

    # 返回的是副本，修改不应影响原数据
    core_events["core.event3"] = CoreEventData
    assert EventRegistry.is_registered("core.event3") is False


def test_list_plugin_events():
    """测试列出所有插件事件"""
    EventRegistry.register_core_event("core.event1", CoreEventData)
    EventRegistry.register_plugin_event("plugin.test.event1", PluginEventData)
    EventRegistry.register_plugin_event("plugin.another.event2", CoreEventData)

    plugin_events = EventRegistry.list_plugin_events()

    # 应该只包含插件事件
    assert len(plugin_events) == 2
    assert "plugin.test.event1" in plugin_events
    assert "plugin.another.event2" in plugin_events
    assert "core.event1" not in plugin_events

    # 返回的是副本，修改不应影响原数据
    plugin_events["plugin.new.event"] = CoreEventData
    assert EventRegistry.is_registered("plugin.new.event") is False


def test_list_all_events():
    """测试列出所有事件"""
    EventRegistry.register_core_event("core.event1", CoreEventData)
    EventRegistry.register_core_event("core.event2", CoreEventData)
    EventRegistry.register_plugin_event("plugin.test.event1", PluginEventData)
    EventRegistry.register_plugin_event("plugin.another.event2", PluginEventData)

    all_events = EventRegistry.list_all_events()

    # 应该包含所有事件
    assert len(all_events) == 4
    assert "core.event1" in all_events
    assert "core.event2" in all_events
    assert "plugin.test.event1" in all_events
    assert "plugin.another.event2" in all_events

    # 返回的是副本，修改不应影响原数据
    all_events["new.event"] = CoreEventData
    assert EventRegistry.is_registered("new.event") is False


def test_list_plugin_events_by_plugin():
    """测试按插件列出事件"""
    # 注册多个插件的事件
    EventRegistry.register_plugin_event("plugin.plugin_a.event1", PluginEventData)
    EventRegistry.register_plugin_event("plugin.plugin_a.event2", CoreEventData)
    EventRegistry.register_plugin_event("plugin.plugin_b.event1", PluginEventData)
    EventRegistry.register_plugin_event("plugin.plugin_b.event2", CoreEventData)
    EventRegistry.register_core_event("core.event1", CoreEventData)

    # 查询 plugin_a 的事件
    plugin_a_events = EventRegistry.list_plugin_events_by_plugin("plugin_a")
    assert len(plugin_a_events) == 2
    assert "plugin.plugin_a.event1" in plugin_a_events
    assert "plugin.plugin_a.event2" in plugin_a_events
    assert "plugin.plugin_b.event1" not in plugin_a_events

    # 查询 plugin_b 的事件
    plugin_b_events = EventRegistry.list_plugin_events_by_plugin("plugin_b")
    assert len(plugin_b_events) == 2
    assert "plugin.plugin_b.event1" in plugin_b_events
    assert "plugin.plugin_b.event2" in plugin_b_events
    assert "plugin.plugin_a.event1" not in plugin_b_events

    # 查询不存在的插件
    nonexist_events = EventRegistry.list_plugin_events_by_plugin("nonexistent")
    assert len(nonexist_events) == 0


# =============================================================================
# 事件注销测试
# =============================================================================

def test_unregister_plugin_events():
    """测试注销指定插件的所有事件"""
    # 注册多个插件的事件
    EventRegistry.register_plugin_event("plugin.plugin_a.event1", PluginEventData)
    EventRegistry.register_plugin_event("plugin.plugin_a.event2", CoreEventData)
    EventRegistry.register_plugin_event("plugin.plugin_b.event1", PluginEventData)
    EventRegistry.register_core_event("core.event1", CoreEventData)

    # 注销 plugin_a
    count = EventRegistry.unregister_plugin_events("plugin_a")

    # 应该注销 2 个事件
    assert count == 2

    # 验证 plugin_a 的事件已被注销
    assert EventRegistry.is_registered("plugin.plugin_a.event1") is False
    assert EventRegistry.is_registered("plugin.plugin_a.event2") is False

    # 验证 plugin_b 和核心事件仍然存在
    assert EventRegistry.is_registered("plugin.plugin_b.event1") is True
    assert EventRegistry.is_registered("core.event1") is True


def test_unregister_nonexistent_plugin():
    """测试注销不存在的插件（应返回0，不报错）"""
    count = EventRegistry.unregister_plugin_events("nonexistent_plugin")
    assert count == 0


def test_unregister_plugin_events_multiple_times():
    """测试多次注销同一插件（应幂等）"""
    EventRegistry.register_plugin_event("plugin.test.event1", PluginEventData)

    # 第一次注销
    count1 = EventRegistry.unregister_plugin_events("test")
    assert count1 == 1

    # 第二次注销（应该返回0）
    count2 = EventRegistry.unregister_plugin_events("test")
    assert count2 == 0


# =============================================================================
# 清理功能测试
# =============================================================================

def test_clear_plugin_events():
    """测试清空所有插件事件"""
    EventRegistry.register_core_event("core.event1", CoreEventData)
    EventRegistry.register_core_event("core.event2", CoreEventData)
    EventRegistry.register_plugin_event("plugin.test.event1", PluginEventData)
    EventRegistry.register_plugin_event("plugin.another.event2", CoreEventData)

    EventRegistry.clear_plugin_events()

    # 插件事件应该被清空
    assert EventRegistry.is_registered("plugin.test.event1") is False
    assert EventRegistry.is_registered("plugin.another.event2") is False

    # 核心事件应该仍然存在
    assert EventRegistry.is_registered("core.event1") is True
    assert EventRegistry.is_registered("core.event2") is True


def test_clear_all():
    """测试清空所有事件（包括核心事件和插件事件）"""
    EventRegistry.register_core_event("core.event1", CoreEventData)
    EventRegistry.register_core_event("core.event2", CoreEventData)
    EventRegistry.register_plugin_event("plugin.test.event1", PluginEventData)
    EventRegistry.register_plugin_event("plugin.another.event2", CoreEventData)

    EventRegistry.clear_all()

    # 所有事件都应该被清空
    assert EventRegistry.is_registered("core.event1") is False
    assert EventRegistry.is_registered("core.event2") is False
    assert EventRegistry.is_registered("plugin.test.event1") is False
    assert EventRegistry.is_registered("plugin.another.event2") is False

    # 列表应该为空
    assert len(EventRegistry.list_all_events()) == 0


# =============================================================================
# 边界情况测试
# =============================================================================

def test_empty_registry():
    """测试空注册表的行为"""
    # 所有查询方法在空注册表上应该正常工作
    assert EventRegistry.is_registered("any.event") is False
    assert EventRegistry.is_core_event("any.event") is False
    assert EventRegistry.is_plugin_event("any.event") is False
    assert EventRegistry.get("any.event") is None
    assert len(EventRegistry.list_all_events()) == 0
    assert len(EventRegistry.list_core_events()) == 0
    assert len(EventRegistry.list_plugin_events()) == 0


def test_mixed_operations():
    """测试混合操作（注册、查询、注销、清理）"""
    # 初始状态：注册一些事件
    EventRegistry.register_core_event("core.event1", CoreEventData)
    EventRegistry.register_plugin_event("plugin.test.event1", PluginEventData)
    EventRegistry.register_plugin_event("plugin.test.event2", CoreEventData)

    assert len(EventRegistry.list_all_events()) == 3

    # 注销部分事件
    count = EventRegistry.unregister_plugin_events("test")
    assert count == 2
    assert len(EventRegistry.list_all_events()) == 1

    # 添加新事件
    EventRegistry.register_plugin_event("plugin.new.event1", PluginEventData)
    assert len(EventRegistry.list_all_events()) == 2

    # 清理所有插件事件
    EventRegistry.clear_plugin_events()
    assert len(EventRegistry.list_all_events()) == 1
    assert EventRegistry.is_registered("core.event1") is True

    # 清理所有事件
    EventRegistry.clear_all()
    assert len(EventRegistry.list_all_events()) == 0


def test_event_name_with_dots():
    """测试包含多个点的事件名"""
    # 核心事件
    EventRegistry.register_core_event("perception.raw_data.generated", CoreEventData)
    assert EventRegistry.is_registered("perception.raw_data.generated")

    # 插件事件
    EventRegistry.register_plugin_event("plugin.my.plugin.nested.event", PluginEventData)
    assert EventRegistry.is_registered("plugin.my.plugin.nested.event")

    # 按插件查询
    events = EventRegistry.list_plugin_events_by_plugin("my")
    assert len(events) == 1
    assert "plugin.my.plugin.nested.event" in events


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
