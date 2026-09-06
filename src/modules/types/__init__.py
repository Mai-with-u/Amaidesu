"""核心类型定义。

跨阶段共享的类型定义,避免循环依赖。
"""

from .capabilities import (
    CapabilitiesProvider,
    UnifiedActionEntry,
    UnifiedCapabilitiesView,
)
from .message_type import (
    MESSAGE_TYPE_REGISTRY,
    MessageTypeNotRegistered,
    MessageTypeRegistrationError,
    MessageTypeSpec,
    clear as clear_message_type_registry,
    get_message_type,
    list_message_types,
    register_message_type,
    require_message_type,
    reset_to_builtins,
)

__all__ = [
    "CapabilitiesProvider",
    "UnifiedActionEntry",
    "UnifiedCapabilitiesView",
    "MESSAGE_TYPE_REGISTRY",
    "MessageTypeSpec",
    "MessageTypeRegistrationError",
    "MessageTypeNotRegistered",
    "register_message_type",
    "require_message_type",
    "get_message_type",
    "list_message_types",
    "clear_message_type_registry",
    "reset_to_builtins",
]
