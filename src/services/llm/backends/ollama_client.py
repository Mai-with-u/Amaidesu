"""
Ollama 客户端实现（占位符）

未来扩展实现，提供本地 Ollama 模型支持。
"""

import asyncio
from src.services.llm.backends.client_base import LLMClient
from typing import List, Dict, Any, Optional, Union


class OllamaClient(LLMClient):
    """
    Ollama 本地模型客户端

    注意：此实现目前为占位符，未来会实现完整的 Ollama 支持。
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger.warning("Ollama 客户端尚未实现完整功能，将降级到 OpenAI 客户端")

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        raise NotImplementedError("Ollama 客户端尚未实现，请使用 openai 客户端")

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> Any:
        raise NotImplementedError("Ollama 客户端尚未实现，请使用 openai 客户端")

    async def vision(
        self,
        messages: List[Dict[str, Any]],
        images: List[Union[str, bytes]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Any:
        raise NotImplementedError("Ollama 客户端尚未实现，请使用 openai 客户端")
