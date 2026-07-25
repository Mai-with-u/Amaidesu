"""LLM 客户端抽象与显式注册表。"""

from __future__ import annotations

import abc
import asyncio
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional, Union

if TYPE_CHECKING:
    from src.modules.llm.manager import LLMResponse


class BaseLLMClient(abc.ABC):
    """LLM 客户端的最小统一接口。

    新的实现只需继承此类，实现必要的抽象方法后调用
    :func:`register_client` 完成注册即可。
    """

    def __init__(self, config: Dict[str, Any]):
        """保存客户端的原始配置。"""
        self.config = config

    @abc.abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        interrupt_flag: Optional[asyncio.Event] = None,
    ) -> LLMResponse:
        """执行一次非流式聊天请求。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stop_event: Optional[asyncio.Event] = None,
        interrupt_flag: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[str]:
        """执行一次流式聊天请求。"""
        raise NotImplementedError

    async def vision(
        self,
        messages: List[Dict[str, Any]],
        images: List[Union[str, bytes]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """执行视觉请求；不支持时由默认实现明确报告。"""
        raise NotImplementedError

    async def cleanup(self) -> None:
        """释放客户端资源；默认无需执行任何操作。"""
        return None

    @classmethod
    def client_type_name(cls) -> str:
        """返回客户端实现的注册标识。"""
        return "unknown"


_client_impls: dict[str, type[BaseLLMClient]] = {}


def register_client(client_type: str, impl: type[BaseLLMClient]) -> None:
    """注册客户端实现。"""
    _client_impls[client_type] = impl


def get_client_impl(client_type: str) -> type[BaseLLMClient]:
    """按类型获取已注册的客户端实现。"""
    if client_type not in _client_impls:
        raise ValueError(f"未注册的客户端类型: {client_type}")
    return _client_impls[client_type]
