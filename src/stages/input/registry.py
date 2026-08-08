"""
装饰器驱动的注册系统 - Input Collectors

使用方式:
    @collector("console")
    class ConsoleInputCollector:
        ...

导入此模块即可填充 _COLLECTORS 字典。

设计原则:
- 极简设计：模块级字典 + 装饰器函数
- 无元类、无注册中心、无服务定位器
- 这些装饰器替换了旧的 Collector/Decider/Handler 注册系统
"""

from typing import Dict, Optional, Type, TypeVar

T = TypeVar("T")

# 模块级注册表
_COLLECTORS: Dict[str, Type] = {}


def collector(
    name: str,
    *,
    label: Optional[str] = None,
    description: Optional[str] = None,
):
    """
    将类注册为 Input Collector 的装饰器

    Args:
        name: 唯一标识符（如 "console", "bili_danmaku"）
        label: 配置页显示名。不传则显示 name 本身（英文标识，不做伪翻译）
        description: 配置页显示描述。不传则为空

    Raises:
        ValueError: 如果名称已被注册

    Returns:
        装饰器函数

    Example:
        @collector("console", label="控制台输入", description="从控制台读取用户输入")
        class ConsoleInputCollector:
            pass
    """

    def decorator(cls: Type[T]) -> Type[T]:
        if name in _COLLECTORS:
            raise ValueError(f"Collector '{name}' 已被注册")
        _COLLECTORS[name] = cls
        # 反向引用：通过实例/类即可查回注册名（与 register_event 的 _registered_event_name 同模式）
        cls._registered_name = name  # type: ignore[attr-defined]

        try:
            from src.modules.config.schemas import (
                register_component_ui,
                register_config_schema,
            )

            # 自动注册 ConfigSchema（如果组件定义了嵌套 ConfigSchema 类）
            config_schema = getattr(cls, "ConfigSchema", None)
            if config_schema is not None:
                register_config_schema(name, config_schema)

            # UI 元数据是组件属性，独立于 ConfigSchema（无嵌套 schema 的组件也可声明显示名）
            register_component_ui(name, label or name, description or "")
        except ImportError:
            pass  # schemas 模块不可用时静默跳过

        return cls

    return decorator


def get_collector(name: str) -> Type:
    """
    通过名称获取已注册的 Collector 类

    Args:
        name: Collector 名称

    Raises:
        KeyError: 如果 Collector 未找到

    Returns:
        Collector 类
    """
    if name not in _COLLECTORS:
        available = list(_COLLECTORS.keys())
        raise KeyError(f"Collector '{name}' 未找到。可用: {available}")
    return _COLLECTORS[name]


def list_collectors() -> list[str]:
    """
    列出所有已注册的 Collector 名称

    Returns:
        Collector 名称列表
    """
    return list(_COLLECTORS.keys())
