"""
LLM 后端抽象基类

定义统一的 LLM 后端接口，不同的 LLM 提供商实现此接口。
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, AsyncIterator, Union

from src.utils.logger import get_logger


class LLMBackend(ABC):
    """
    LLM 后端抽象基类

    不同的 LLM 提供商实现此接口：
    - OpenAIBackend: OpenAI 兼容 API（包括 SiliconFlow、DeepSeek 等）
    - OllamaBackend: 本地 Ollama
    - AnthropicBackend: Claude API
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        """
        聊天调用

        Args:
            messages: 消息列表（OpenAI 格式）
            temperature: 温度参数
            max_tokens: 最大 token 数
            tools: 工具定义（OpenAI 格式）

        Returns:
            LLMResponse 对象
        """
        pass

    @abstractmethod
    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[str]:
        """
        流式聊天

        Args:
            messages: 消息列表（OpenAI 格式）
            temperature: 温度参数
            max_tokens: 最大 token 数
            stop_event: 停止事件（用于中断流式输出）

        Yields:
            str: 增量文本内容
        """
        pass

    @abstractmethod
    async def vision(
        self,
        messages: List[Dict[str, Any]],
        images: List[Union[str, bytes]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Any:
        """
        视觉理解

        Args:
            messages: 消息列表（OpenAI 格式）
            images: 图片列表（URL、路径或字节）
            temperature: 温度参数
            max_tokens: 最大 token 数

        Returns:
            LLMResponse 对象
        """
        pass

    async def cleanup(self) -> None:  # noqa: B027
        """清理资源（子类可选重写）"""
        pass

    def get_info(self) -> Dict[str, Any]:
        """
        获取后端信息

        Returns:
            后端信息字典
        """
        return {
            "name": self.__class__.__name__,
            "model": self.config.get("model"),
            "base_url": self.config.get("base_url"),
        }
