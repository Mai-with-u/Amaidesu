"""
事件注册表

支持两种事件类型：
- 核心事件：系统内部使用，只读
- 插件事件：社区插件使用，开放注册

验证策略：
- 核心事件：强制验证（debug 模式）
- 插件事件：可选验证
- 未注册事件：允许发布，仅警告
"""

from typing import Dict, Type, Optional
from pydantic import BaseModel

from src.utils.logger import get_logger


class EventRegistry:
    """
    事件类型注册表

    支持两种事件类型：
    - 核心事件：系统内部使用，只读
    - 插件事件：社区插件使用，开放注册

    验证策略：
    - 核心事件：强制验证（debug 模式）
    - 插件事件：可选验证
    - 未注册事件：允许发布，仅警告
    """

    # 核心事件（只读）
    _core_events: Dict[str, Type[BaseModel]] = {}
    # 插件事件（开放）
    _plugin_events: Dict[str, Type[BaseModel]] = {}

    _logger = get_logger("EventRegistry")

    # ==================== 核心事件 API ====================

    @classmethod
    def register_core_event(cls, event_name: str, model: Type[BaseModel]) -> None:
        """
        注册核心事件（仅内部使用）

        Args:
            event_name: 事件名称（如 "perception.raw_data.generated"）
            model: Pydantic Model 类型

        Raises:
            ValueError: 事件名不符合核心事件命名规范
        """
        # 验证命名规范
        valid_prefixes = (
            "perception.",
            "normalization.",
            "decision.",
            "understanding.",
            "expression.",
            "render.",
            "core.",
        )
        if not any(event_name.startswith(prefix) for prefix in valid_prefixes):
            raise ValueError(f"核心事件名必须以 {valid_prefixes} 之一开头，收到: {event_name}")

        if event_name in cls._core_events:
            cls._logger.warning(f"核心事件已存在，将覆盖: {event_name}")

        cls._core_events[event_name] = model
        cls._logger.debug(f"注册核心事件: {event_name} -> {model.__name__}")

    # ==================== 插件事件 API ====================

    @classmethod
    def register_plugin_event(cls, event_name: str, model: Type[BaseModel]) -> None:
        """
        注册插件事件（对社区开放）

        命名约定：plugin.{plugin_name}.{event_name}

        Args:
            event_name: 事件名称（必须以 "plugin." 开头）
            model: Pydantic Model 类型

        Raises:
            ValueError: 事件名不符合插件事件命名规范
        """
        if not event_name.startswith("plugin."):
            raise ValueError(
                f"插件事件名必须以 'plugin.' 开头，收到: {event_name}。正确格式: plugin.{{plugin_name}}.{{event_name}}"
            )

        # 解析插件名
        parts = event_name.split(".")
        if len(parts) < 3:
            raise ValueError(f"插件事件名格式错误: {event_name}。正确格式: plugin.{{plugin_name}}.{{event_name}}")

        if event_name in cls._plugin_events:
            cls._logger.warning(f"插件事件已存在，将覆盖: {event_name}")

        cls._plugin_events[event_name] = model
        cls._logger.debug(f"注册插件事件: {event_name} -> {model.__name__}")

    # ==================== 查询 API ====================

    @classmethod
    def get(cls, event_name: str) -> Optional[Type[BaseModel]]:
        """
        获取事件的 Model 类型（核心事件优先）

        Args:
            event_name: 事件名称

        Returns:
            Pydantic Model 类型，未注册返回 None
        """
        return cls._core_events.get(event_name) or cls._plugin_events.get(event_name)

    @classmethod
    def is_registered(cls, event_name: str) -> bool:
        """检查事件是否已注册"""
        return event_name in cls._core_events or event_name in cls._plugin_events

    @classmethod
    def is_core_event(cls, event_name: str) -> bool:
        """检查是否为核心事件"""
        return event_name in cls._core_events

    @classmethod
    def is_plugin_event(cls, event_name: str) -> bool:
        """检查是否为插件事件"""
        return event_name in cls._plugin_events

    # ==================== 列表 API ====================

    @classmethod
    def list_core_events(cls) -> Dict[str, Type[BaseModel]]:
        """列出所有核心事件"""
        return cls._core_events.copy()

    @classmethod
    def list_plugin_events(cls) -> Dict[str, Type[BaseModel]]:
        """列出所有插件事件"""
        return cls._plugin_events.copy()

    @classmethod
    def list_all_events(cls) -> Dict[str, Type[BaseModel]]:
        """列出所有注册的事件"""
        return {**cls._core_events, **cls._plugin_events}

    @classmethod
    def list_plugin_events_by_plugin(cls, plugin_name: str) -> Dict[str, Type[BaseModel]]:
        """
        列出指定插件的所有事件

        Args:
            plugin_name: 插件名称

        Returns:
            该插件的所有事件
        """
        prefix = f"plugin.{plugin_name}."
        return {name: model for name, model in cls._plugin_events.items() if name.startswith(prefix)}

    # ==================== 清理 API ====================

    @classmethod
    def unregister_plugin_events(cls, plugin_name: str) -> int:
        """
        注销指定插件的所有事件（插件 cleanup 时调用）

        Args:
            plugin_name: 插件名称

        Returns:
            注销的事件数量
        """
        prefix = f"plugin.{plugin_name}."
        to_remove = [name for name in cls._plugin_events if name.startswith(prefix)]

        for name in to_remove:
            del cls._plugin_events[name]
            cls._logger.debug(f"注销插件事件: {name}")

        return len(to_remove)

    @classmethod
    def clear_plugin_events(cls) -> None:
        """清空所有插件事件（仅用于测试）"""
        cls._plugin_events.clear()
        cls._logger.info("已清空所有插件事件")

    @classmethod
    def clear_all(cls) -> None:
        """清空所有事件（仅用于测试）"""
        cls._core_events.clear()
        cls._plugin_events.clear()
        cls._logger.info("已清空所有事件")
