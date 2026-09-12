"""LLM 客户端抽象与显式注册表。"""

from __future__ import annotations

import abc
import asyncio
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Dict, List, Optional, Union

if TYPE_CHECKING:
    from src.modules.llm.manager import LLMResponse

OnDeltaCallback = Callable[[str, str], None]
"""流式增量回调：(kind, text_delta)，kind ∈ {"reasoning", "content"}。"""


class BaseLLMClient(abc.ABC):
    """LLM 客户端的最小统一接口。

    新的实现只需继承此类，实现必要的抽象方法后调用
    :func:`register_client` 完成注册即可。

    设计约定：客户端按 provider 维度共享（一个 provider 一个连接实例），
    model 由调用方每次请求显式传入——LLMManager 按 ``llm_profiles.<name>``
    的 ``model_list`` 做选择与故障切换。
    """

    def __init__(self, config: Dict[str, Any]):
        """保存客户端的原始 provider 配置（连接信息/鉴权/超时/重试/默认 headers）。"""
        self.config = config

    @abc.abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        interrupt_flag: Optional[asyncio.Event] = None,
        on_delta: Optional[OnDeltaCallback] = None,
    ) -> LLMResponse:
        """执行一次聊天请求。

        ``model`` 必填：客户端不持有默认模型，由 LLMManager 按 profile 选定后传入。
        ``on_delta`` 非 None 时实现方应走流式传输并逐帧回调增量，
        最终仍返回完整 LLMResponse（传输层流式、语义层整段）。
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stop_event: Optional[asyncio.Event] = None,
        interrupt_flag: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[str]:
        """执行一次流式聊天（model 必填）。"""
        raise NotImplementedError

    async def vision(
        self,
        messages: List[Dict[str, Any]],
        images: List[Union[str, bytes]],
        *,
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """执行视觉请求；不支持时由默认实现明确报告。model 必填。"""
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
