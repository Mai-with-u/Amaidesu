"""LLM 客户端实现模块

提供不同 LLM 提供商的客户端实现：
- OpenAIClient: OpenAI 兼容 API（包括 SiliconFlow、DeepSeek、Ollama 等所有兼容 API）

注意：Ollama、LM Studio、vLLAM 等本地模型都提供 OpenAI 兼容 API，
只需配置 base_url 即可使用。
"""

from .client_base import LLMClient
from .openai_client import OpenAIClient

__all__ = [
    "LLMClient",
    "OpenAIClient",
]
