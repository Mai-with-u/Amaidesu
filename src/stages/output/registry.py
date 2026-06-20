"""
装饰器驱动的注册系统 - Output Handlers

使用方式:
    @handler("tts")
    class TTSOutputHandler:
        ...

导入此模块即可填充 _HANDLERS 字典。

设计原则:
- 极简设计：模块级字典 + 装饰器函数
- 无元类、无注册中心、无服务定位器
- 这些装饰器替换了旧的 Collector/Decider/Handler 注册系统
"""

from typing import TypeVar, Type, Dict

T = TypeVar("T")

# 模块级注册表
_HANDLERS: Dict[str, Type] = {}


def handler(name: str):
    """
    将类注册为 Output Handler 的装饰器

    Args:
        name: 唯一标识符（如 "tts", "subtitle"）

    Raises:
        ValueError: 如果名称已被注册

    Returns:
        装饰器函数

    Example:
        @handler("tts")
        class TTSOutputHandler:
            pass
    """

    def decorator(cls: Type[T]) -> Type[T]:
        if name in _HANDLERS:
            raise ValueError(f"Handler '{name}' 已被注册")
        _HANDLERS[name] = cls
        return cls

    return decorator


def get_handler(name: str) -> Type:
    """
    通过名称获取已注册的 Handler 类

    Args:
        name: Handler 名称

    Raises:
        KeyError: 如果 Handler 未找到

    Returns:
        Handler 类
    """
    if name not in _HANDLERS:
        available = list(_HANDLERS.keys())
        raise KeyError(f"Handler '{name}' 未找到。可用: {available}")
    return _HANDLERS[name]


def list_handlers() -> list[str]:
    """
    列出所有已注册的 Handler 名称

    Returns:
        Handler 名称列表
    """
    return list(_HANDLERS.keys())
