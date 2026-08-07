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

from typing import TYPE_CHECKING, Dict, Optional, Protocol, Type, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from src.modules.types.capabilities import HandlerCapabilities

T = TypeVar("T")

# 模块级注册表
_HANDLERS: Dict[str, Type] = {}


def handler(
    name: str,
    *,
    label: Optional[str] = None,
    description: Optional[str] = None,
):
    """
    将类注册为 Output Handler 的装饰器

    Args:
        name: 唯一标识符（如 "tts", "subtitle"）
        label: 配置页显示名。不传则显示 name 本身（英文标识，不做伪翻译）
        description: 配置页显示描述。不传则为空

    Raises:
        ValueError: 如果名称已被注册

    Returns:
        装饰器函数

    Example:
        @handler("subtitle", label="字幕", description="字幕渲染")
        class SubtitleHandler:
            pass
    """

    def decorator(cls: Type[T]) -> Type[T]:
        if name in _HANDLERS:
            raise ValueError(f"Handler '{name}' 已被注册")
        _HANDLERS[name] = cls

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


@runtime_checkable
class SupportsCapabilities(Protocol):
    """声明 handler 实现了 `get_capabilities()` 的 Protocol。

    - **只强制** `get_capabilities()` 方法,handler 内部可自由选填
      `_ACTION_PARAMS_SCHEMA` / `_EMOTION_KEYS` 等约定属性
    - 用 `isinstance(h, SupportsCapabilities)` 做运行时检查
    - 用于 `OutputHandlerManager.get_all_capabilities()` 决定是否给该 handler
      加前缀并展开其 actions
    """

    def get_capabilities(self) -> "HandlerCapabilities":
        """返回 handler 暴露的能力(本地 action 名)。"""
        ...


__all__ = [
    "handler",
    "get_handler",
    "list_handlers",
    "SupportsCapabilities",
]
