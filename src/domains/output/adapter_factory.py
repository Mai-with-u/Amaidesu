"""
Adapter Factory - 适配器工厂
"""

from typing import Dict, Any, Optional

from .adapters.base import PlatformAdapter
from .adapters.vts.vts_adapter import VTSAdapter
from .adapters.vrchat.vrchat_adapter import VRChatAdapter

from src.core.utils.logger import get_logger


class AdapterFactory:
    """适配器工厂

    负责根据配置创建平台适配器实例。
    """

    _adapters: Dict[str, type] = {
        "vts": VTSAdapter,
        "vrchat": VRChatAdapter,
        "vrc": VRChatAdapter,  # 别名
        # 未来可以添加更多平台
        # "live2d": Live2DAdapter,
    }

    @classmethod
    def register_adapter(cls, name: str, adapter_class: type) -> None:
        """注册新的适配器类型

        Args:
            name: 适配器名称
            adapter_class: 适配器类（必须是 PlatformAdapter 的子类）
        """
        if not issubclass(adapter_class, PlatformAdapter):
            raise TypeError(f"{adapter_class} 必须是 PlatformAdapter 的子类")

        cls._adapters[name] = adapter_class
        get_logger("AdapterFactory").info(f"注册适配器: {name}")

    @classmethod
    def create(cls, adapter_type: str, config: Dict[str, Any]) -> Optional[PlatformAdapter]:
        """创建适配器实例

        Args:
            adapter_type: 适配器类型（如 "vts", "vrc", "live2d"）
            config: 配置字典

        Returns:
            适配器实例，如果类型未知返回 None
        """
        adapter_class = cls._adapters.get(adapter_type)
        if not adapter_class:
            get_logger("AdapterFactory").error(f"未知的适配器类型: {adapter_type}")
            return None

        try:
            return adapter_class(config)
        except Exception as e:
            get_logger("AdapterFactory").error(f"创建 {adapter_type} 适配器失败: {e}", exc_info=True)
            return None

    @classmethod
    def list_available_adapters(cls) -> list[str]:
        """列出所有可用的适配器类型

        Returns:
            适配器类型名称列表
        """
        return list(cls._adapters.keys())
