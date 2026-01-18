"""
Canonical模块 - Layer 3: 中间表示层

职责:
- 定义统一的中间表示格式(CanonicalMessage)
- 支持元数据传递
- 支持DataCache引用
- 提供MessageBase兼容性
"""

from .canonical_message import CanonicalMessage, MessageBuilder

__all__ = ["CanonicalMessage", "MessageBuilder"]
