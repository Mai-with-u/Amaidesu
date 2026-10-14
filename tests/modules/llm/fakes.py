"""LLM 测试共用替身：SDK 响应/流式分片、带 SDK 替身的 OpenAIClient、挂真实客户端的 LLMManager。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.clients.openai.client import OpenAIClient
from src.modules.llm.engine import LLMManager

SDK_PATH = "src.modules.llm.clients.openai.client.AsyncOpenAI"


def sdk_reply(
    *, content: Any = "world", model: str = "test-model", reasoning_content=None, tool_calls=None, usage=None
):
    """非流式 chat.completions 返回值。"""
    message = SimpleNamespace(content=content, reasoning_content=reasoning_content, tool_calls=tool_calls or [])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], model=model, usage=usage)


def sdk_tool_reply(arguments: str, finish_reason: str = "tool_calls", content: str = "") -> SimpleNamespace:
    """带一个 validate 工具调用的回复，保留服务端实际结束原因。"""
    call = SimpleNamespace(
        id="design-call", type="function", function=SimpleNamespace(name="validate", arguments=arguments)
    )
    message = SimpleNamespace(content=content, tool_calls=[call], reasoning_content=None)
    return SimpleNamespace(
        model="test-model", usage=None, choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
    )


def usage(prompt: int = 10, completion: int = 5) -> SimpleNamespace:
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion)


class FakeStream:
    """模拟 SSE 流（async iterator + aclose）。"""

    def __init__(self, chunks) -> None:
        self._chunks = list(chunks)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def aclose(self) -> None:
        self.closed = True


def chunk(reasoning=None, content=None, tool_calls=None, usage=None, finish_reason=None):
    delta = SimpleNamespace(reasoning_content=reasoning, content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage, model="test-model")


def tool_delta(index, name=None, arguments=None, call_id=None):
    return SimpleNamespace(index=index, id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def make_openai_client(config: dict[str, object] | None = None) -> tuple[OpenAIClient, MagicMock]:
    """真实 OpenAIClient + SDK 替身（create 缺省返回 sdk_reply()）。"""
    merged: dict[str, object] = {"api_key": "test-key", "base_url": "https://api.example.com/v1", "model": "test-model"}
    merged.update(config or {})
    with patch(SDK_PATH) as openai_class:
        sdk = MagicMock()
        sdk.chat.completions.create = AsyncMock(return_value=sdk_reply())
        sdk.close = AsyncMock()
        openai_class.return_value = sdk
        client = OpenAIClient(merged)
    return client, sdk


@asynccontextmanager
async def engine_with(client: Any, profiles: dict[str, dict], **provider: Any) -> AsyncIterator[LLMManager]:
    """单 provider「p」+ 单模型「m」(test-model) 的 LLMManager，客户端用传入实例。"""
    config = {
        "llm_providers": [
            {
                "name": "p",
                "client_type": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "test",
                **provider,
            }
        ],
        "llm_models": [{"name": "m", "model_identifier": "test-model", "api_provider": "p"}],
        "llm_profiles": {name: {"model_list": ["m"], **fields} for name, fields in profiles.items()},
    }
    manager = LLMManager()
    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=client)}):
        await manager.setup(config)
    try:
        yield manager
    finally:
        await manager.cleanup()
