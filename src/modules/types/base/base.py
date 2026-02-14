"""
Provider接口基础数据类定义

定义了新架构中的核心数据结构:
- NormalizedMessage: 标准化消息 (来自 Input Domain)
- ProviderManager Protocol: Provider 管理器接口
"""

# NormalizedMessage 定义在 src/modules/types/base/normalized_message.py
from src.modules.types.base.normalized_message import NormalizedMessage

# ProviderManager 接口定义
from src.modules.types.base.provider_manager import (
    BaseProviderManager,
    InputProviderManagerProtocol,
    DecisionProviderManagerProtocol,
    OutputProviderManagerProtocol,
)

__all__ = [
    "NormalizedMessage",
    "BaseProviderManager",
    "InputProviderManagerProtocol",
    "DecisionProviderManagerProtocol",
    "OutputProviderManagerProtocol",
]
