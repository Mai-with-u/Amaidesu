"""LLM 服务模块

提供 LLM 客户端管理功能：
- LLMManager: LLM 客户端管理器
- OpenAIClient: OpenAI API 客户端
- OllamaClient: Ollama 本地模型客户端
"""

from . import backends
from .manager import LLMManager

__all__ = [
    "LLMManager",
    "backends",
]
