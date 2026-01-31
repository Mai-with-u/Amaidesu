"""
Canonical模块 - Layer 3: 中间表示层

职责:
- 定义统一的中间表示格式(CanonicalMessage)
- 支持元数据传递
- 支持DataCache引用
- 提供MessageBase兼容性
- CanonicalLayer: Layer 2→3 桥接
"""

from .canonical_message import CanonicalMessage, MessageBuilder
from .canonical_layer import CanonicalLayer

__all__ = ["CanonicalMessage", "MessageBuilder", "CanonicalLayer"]
