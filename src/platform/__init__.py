"""
Platform Layer - 平台抽象层

职责:
- 提供平台无关的虚拟形象控制接口
- 将抽象表情参数翻译为平台特定参数
- 支持多个平台（VTS、VRChat、Live2D）
"""

from .adapter_factory import AdapterFactory
from .adapters.base import PlatformAdapter

__all__ = ["PlatformAdapter", "AdapterFactory"]
