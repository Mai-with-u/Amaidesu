"""LLM 客户端实现模块

提供 OpenAI 兼容 API 的客户端实现。
注意：Ollama、LM Studio、vLLAM 等本地模型都提供 OpenAI 兼容 API，
只需配置 base_url 即可使用。
"""

from .openai_client import OpenAIClient

__all__ = [
    "OpenAIClient",
]
