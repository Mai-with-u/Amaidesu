"""
Input Domain - 输入域

包含数据采集和消息标准化的实现。
负责将外部数据转换为NormalizedMessage。
"""

from .input_domain import InputDomain
from .input_provider_manager import InputProviderManager

__all__ = ["InputDomain", "InputProviderManager"]
