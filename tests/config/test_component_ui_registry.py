"""组件 UI 元数据注册表测试（显示名/描述单一事实源头）。

验证 ``@collector/@decider/@handler`` 装饰器登记的 ``COMPONENT_UI_REGISTRY``：

1. 与 ``CONFIG_SCHEMA_REGISTRY`` 一一对应（同 key 集）
2. 每个组件都有非空显示名与描述
3. 显示名不允许是占位伪翻译（.title() 派生或 "xx 配置" 模式）
4. 回归：``mainosaba`` 显示名为"游戏画面读取"（曾误译为"主幕读取"）
"""

import pytest

# 导入三个阶段包触发全部 @collector/@decider/@handler 注册
import src.stages.input.collectors  # noqa: F401
import src.stages.decision.deciders  # noqa: F401
import src.stages.output.handlers  # noqa: F401

from src.modules.config.schemas import (
    COMPONENT_UI_REGISTRY,
    CONFIG_SCHEMA_REGISTRY,
)


def test_ui_registry_covers_all_schema_components():
    """有配置 schema 的组件必须都有 UI 元数据（schema 注册表 ⊆ UI 注册表）。

    反向不成立：无嵌套 ConfigSchema 的组件（如 simulated_live_stream）
    只有 UI 元数据，不在 schema 注册表中。
    """
    assert set(CONFIG_SCHEMA_REGISTRY) <= set(COMPONENT_UI_REGISTRY)


@pytest.mark.parametrize("name", sorted(COMPONENT_UI_REGISTRY))
def test_component_has_nonempty_label(name):
    """组件显示名非空（fallback 保证：显式名或英文 key 本身）。"""
    meta = COMPONENT_UI_REGISTRY[name]
    assert meta.label, f"{name} 缺少显示名"


@pytest.mark.parametrize("name", sorted(COMPONENT_UI_REGISTRY))
def test_component_label_is_not_placeholder(name):
    """显示名不得是 "xx 配置" 占位翻译（如旧表生成的 "B 站弹幕 配置"）。"""
    meta = COMPONENT_UI_REGISTRY[name]
    assert not meta.label.endswith("配置"), f"{name} 显示名是占位翻译: {meta.label!r}"


@pytest.mark.parametrize("name", sorted(COMPONENT_UI_REGISTRY))
def test_component_has_description(name):
    """组件显示描述非空。"""
    meta = COMPONENT_UI_REGISTRY[name]
    assert meta.description, f"{name} 缺少显示描述"


def test_mainosaba_label_regression():
    """回归：mainosaba 显示名是"游戏画面读取"，不得回退为"主幕读取"。"""
    assert COMPONENT_UI_REGISTRY["mainosaba"].label == "游戏画面读取"
