"""
事件注册一致性硬检查测试

``ensure_registry_consistency()`` 的启动契约：
- 正向：register_core_events() 后注册集合与 CoreEvents 具名事件完全一致
- 反向：缺注册 / 多注册时报错并指名
- 动态族（tool.result.* / tool.health.*）前缀下的名字不算多余

本文件自带注册状态管理（不与 test_event_registry.py 的清空 fixture 交互）：
EVENT_REGISTRY 的填充依赖 payload 模块首次 import 的装饰器执行，测试只做
临时增删并在 finally 恢复，不做整体清空。

运行: uv run pytest tests/modules/events/test_registry_consistency.py -v
"""

import pytest

from src.modules.events.names import CoreEvents
from src.modules.events.registry import (
    DYNAMIC_EVENT_FAMILIES,
    EVENT_REGISTRY,
    ensure_registry_consistency,
    register_core_events,
)


@pytest.fixture(scope="module", autouse=True)
def populated_registry():
    """模块级：确保注册表已填充，结束后恢复进入时快照"""
    saved = dict(EVENT_REGISTRY)
    register_core_events()
    yield
    EVENT_REGISTRY.clear()
    EVENT_REGISTRY.update(saved)


def test_registry_consistent_with_core_events():
    """正向：注册集合与 CoreEvents 具名事件完全一致，检查通过"""
    # 不抛错即一致
    ensure_registry_consistency()

    expected = {n for n in CoreEvents.get_all_events() if "*" not in n and "#" not in n}
    assert set(EVENT_REGISTRY.keys()) == expected
    # 动态族已随 register_core_events 登记
    assert "tool.result." in DYNAMIC_EVENT_FAMILIES
    assert "tool.health." in DYNAMIC_EVENT_FAMILIES


def test_missing_registration_raises():
    """反向：移除一个注册 → 检查报错并指出缺失事件名"""
    saved = EVENT_REGISTRY.pop("game.milestone")

    try:
        with pytest.raises(RuntimeError, match="game.milestone"):
            ensure_registry_consistency()
    finally:
        EVENT_REGISTRY["game.milestone"] = saved


def test_extra_registration_raises():
    """反向：塞入 CoreEvents 未定义且不属于动态族的注册 → 检查报错"""
    from pydantic import BaseModel

    class RoguePayload(BaseModel):
        pass

    EVENT_REGISTRY["rogue.event.name"] = RoguePayload

    try:
        with pytest.raises(RuntimeError, match="rogue.event.name"):
            ensure_registry_consistency()
    finally:
        EVENT_REGISTRY.pop("rogue.event.name", None)


def test_dynamic_family_member_not_flagged_extra():
    """动态族前缀下的注册名不算多余（检查只对族外名字报警）"""
    from src.modules.events.payloads.tool_result import ToolResultPayload

    EVENT_REGISTRY["tool.result.some_tool"] = ToolResultPayload

    try:
        ensure_registry_consistency()  # 不抛错
    finally:
        EVENT_REGISTRY.pop("tool.result.some_tool", None)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
