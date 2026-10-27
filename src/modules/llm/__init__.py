"""LLM 服务模块

模块按职责分块：
- engine: 调用编排（选型/重试/切换）
- bootstrap: 装配（provider 池/模型索引/profile 解析）
- clients: 厂商适配端（一厂商一目录，经调度表接入）
- observation: 用量记账；payload: 中立契约（后续就位）
"""

from . import clients
from .capabilities import ProfileContextWindowLookup, ProfileVisualCapabilityLookup, validate_llm_capability
from .client import BaseLLMClient
from .engine import LLMManager

__all__ = [
    "BaseLLMClient",
    "LLMManager",
    "ProfileContextWindowLookup",
    "ProfileVisualCapabilityLookup",
    "clients",
    "validate_llm_capability",
]
