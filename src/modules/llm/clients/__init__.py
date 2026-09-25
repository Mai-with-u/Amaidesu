"""LLM 适配端调度层

引擎经由本包取得厂商客户端实现与能力方法，不直接 import 任何
``clients/<vendor>/`` 具体模块。新增厂商 = 新增 ``clients/<vendor>/`` 目录 +
在下方调度表追加一行。

注意：Ollama、LM Studio、vLLM 等本地模型都提供 OpenAI 兼容 API，
只需配置 base_url 即可复用 openai 适配端。
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from src.modules.llm.client import BaseLLMClient
from src.modules.llm.clients.openai.client import OpenAIClient
from src.modules.llm.clients.openai.compat import (
    OpenAICompatibleAuthType,
    OpenAICompatibleClientConfig,
    build_openai_compatible_client_config,
    normalize_openai_base_url,
)
from src.modules.llm.reasoning import ReasoningParseMode, parse_reasoning

__all__ = [
    "BaseLLMClient",
    "OpenAIClient",
    "_CLIENT_DISPATCH",
    "get_client_impl",
    "resolve_client_method",
    "OpenAICompatibleClientConfig",
    "OpenAICompatibleAuthType",
    "build_openai_compatible_client_config",
    "normalize_openai_base_url",
    "ReasoningParseMode",
    "parse_reasoning",
]

# 调度表：client_type → 客户端实现类（显式映射，新厂商在此追加一行）。
# 测试注入 fake 实现时 patch 本字典即可。
_CLIENT_DISPATCH: Dict[str, type[BaseLLMClient]] = {
    "openai": OpenAIClient,
}


def get_client_impl(client_type: str) -> type[BaseLLMClient]:
    """按类型从调度表获取客户端实现类（未知类型 fail-fast）。"""
    if client_type not in _CLIENT_DISPATCH:
        raise ValueError(f"未注册的客户端类型: {client_type}（调度表：{sorted(_CLIENT_DISPATCH)}）")
    return _CLIENT_DISPATCH[client_type]


# 能力调度表：方法名 → 取该能力的访问器（替代引擎层的 getattr 动态分派）。
# generate / generate_vision 是中立 payload 契约，也是引擎仅有的两个入口。
_CLIENT_METHODS: Dict[str, Callable[[BaseLLMClient], Callable[..., Any]]] = {
    "generate": lambda client: client.generate,
    "generate_vision": lambda client: client.generate_vision,
}


def resolve_client_method(client: BaseLLMClient, method: str) -> Callable[..., Any]:
    """按能力名从客户端取绑定的可调用方法（调度表显式映射，未知能力 fail-fast）。"""
    resolver = _CLIENT_METHODS.get(method)
    if resolver is None:
        raise ValueError(f"未知的客户端能力: {method}（客户端类型: {type(client).__name__}）")
    return resolver(client)
