"""LLM 后端实现模块

提供不同 LLM 提供商的后端实现：
- OpenAIBackend: OpenAI 兼容 API（包括 SiliconFlow、DeepSeek 等）
- OllamaBackend: 本地 Ollama 模型
- AnthropicBackend: Claude API（未来扩展）
"""

from .base import LLMBackend

__all__ = ["LLMBackend"]
