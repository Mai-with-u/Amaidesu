"""LLM 客户端实现模块

提供 OpenAI 兼容 API 的客户端实现。
注意：Ollama、LM Studio、vLLAM 等本地模型都提供 OpenAI 兼容 API，
只需配置 base_url 即可使用。
"""

from .base import BaseLLMClient, get_client_impl, register_client
from .interrupt import await_with_timeout_and_interrupt
from .openai_client import OpenAIClient
from .openai_compat import (
    OpenAICompatibleAuthType,
    OpenAICompatibleClientConfig,
    build_openai_compatible_client_config,
    normalize_openai_base_url,
)
from .reasoning import ReasoningParseMode, parse_reasoning

__all__ = [
    "BaseLLMClient",
    "OpenAIClient",
    "register_client",
    "get_client_impl",
    "OpenAICompatibleClientConfig",
    "OpenAICompatibleAuthType",
    "build_openai_compatible_client_config",
    "normalize_openai_base_url",
    "ReasoningParseMode",
    "parse_reasoning",
    "await_with_timeout_and_interrupt",
]
