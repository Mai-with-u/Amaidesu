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

from typing import TypeVar, Type, Dict

T = TypeVar("T")

# 模块级注册表
_COLLECTORS: Dict[str, Type] = {}


def collector(name: str):
    """
    将类注册为 Input Collector 的装饰器

    Args:
        name: 唯一标识符（如 "console", "bili_danmaku"）

    Raises:
        ValueError: 如果名称已被注册

    Returns:
        装饰器函数

    Example:
        @collector("console")
        class ConsoleInputCollector:
            pass
    """

    def decorator(cls: Type[T]) -> Type[T]:
        if name in _COLLECTORS:
            raise ValueError(f"Collector '{name}' 已被注册")
        _COLLECTORS[name] = cls
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
