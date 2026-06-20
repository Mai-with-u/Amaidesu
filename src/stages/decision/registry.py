"""
装饰器驱动的注册系统 - Decision Deciders

使用方式:
    @decider("maibot")
    class MaiBotDecider:
        ...

导入此模块即可填充 _DECIDERS 字典。

设计原则:
- 极简设计：模块级字典 + 装饰器函数
- 无元类、无注册中心、无服务定位器
- 在 Wave 2 中，这些装饰器替换了所有组件 类
"""

from typing import TypeVar, Type, Dict

T = TypeVar("T")

# 模块级注册表
_DECIDERS: Dict[str, Type] = {}


def decider(name: str):
    """
    将类注册为 Decision Decider 的装饰器

    Args:
        name: 唯一标识符（如 "maibot", "llm"）

    Raises:
        ValueError: 如果名称已被注册

    Returns:
        装饰器函数

    Example:
        @decider("maibot")
        class MaiBotDecider:
            pass
    """

    def decorator(cls: Type[T]) -> Type[T]:
        if name in _DECIDERS:
            raise ValueError(f"Decider '{name}' 已被注册")
        _DECIDERS[name] = cls

        # 自动注册 ConfigSchema（如果组件定义了嵌套 ConfigSchema 类）
        config_schema = getattr(cls, "ConfigSchema", None)
        if config_schema is not None:
            try:
                from src.modules.config.schemas import register_config_schema

                register_config_schema(name, config_schema)
            except ImportError:
                pass  # schemas 模块不可用时静默跳过

        return cls

    return decorator


def get_decider(name: str) -> Type:
    """
    通过名称获取已注册的 Decider 类

    Args:
        name: Decider 名称

    Raises:
        KeyError: 如果 Decider 未找到

    Returns:
        Decider 类
    """
    if name not in _DECIDERS:
        available = list(_DECIDERS.keys())
        raise KeyError(f"Decider '{name}' 未找到。可用: {available}")
    return _DECIDERS[name]


def list_deciders() -> list[str]:
    """
    列出所有已注册的 Decider 名称

    Returns:
        Decider 名称列表
    """
    return list(_DECIDERS.keys())
