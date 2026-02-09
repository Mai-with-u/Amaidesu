"""
Input Domain - 输入域

包含数据采集和消息标准化的实现。
负责将外部数据转换为NormalizedMessage。
"""

from .coordinator import InputCoordinator
from .provider_manager import InputProviderManager

__all__ = ["InputCoordinator", "InputProviderManager"]
