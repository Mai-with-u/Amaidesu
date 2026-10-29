"""LLM 测试共用替身：SDK 响应/流式分片、带 SDK 替身的 OpenAIClient、挂真实客户端的 LLMManager、假 LLMManager。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from src.modules.llm.clients.openai.client import OpenAIClient
from src.modules.llm.engine import LLMManager
from src.modules.llm.payload import Response, Usage

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
    """单 provider「p」+ 单模型「m」(test-model) 的 LLMManager，客户端经注入工厂替换，走完整 setup 装配路径。"""
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
    manager = LLMManager(client_factory=lambda _cfg: client)
    await manager.setup(config)
    try:
        yield manager
    finally:
        await manager.cleanup()


class FakeLLMManager:
    """全库唯一的 LLMManager 测试替身（duck-type，不依赖真实装配）。

    三态行为（互斥，按优先级 error > fail > 正常应答）：

    - ``error``：调用即抛该异常（模拟客户端异常）
    - ``fail``：调用即返回失败 ``Response``（模拟引擎侧故障切换穷尽后的失败响应）
    - 正常应答：给 ``responses`` 时按队列逐次弹出，弹尽后再调用即抛
      ``AssertionError``（未预期调用即红）；未给时反复返回 ``response``
      （缺省一个成功空应答）

    给 ``gate`` 时每次调用先停在闸门前，测试放行才应答（编排"调用在飞"的时序），
    有调用停到闸门前即置位 ``gate_reached``；停在闸门前被取消的调用不记录。

    每次调用记录在 ``calls``（``{"method", "input", **kwargs}``）。另带
    ``is_profile_visual`` / ``get_model_context_windows`` 等装配查询出口，
    供能力协商与 dashboard 只读消费方使用。
    """

    def __init__(
        self,
        response: Optional[Response] = None,
        *,
        error: Optional[Exception] = None,
        fail: bool = False,
        fail_error: str = "not used",
        responses: Optional[List[Response]] = None,
        visual: bool = False,
        context_windows: Optional[Dict[str, int]] = None,
        gate: Optional[asyncio.Event] = None,
    ) -> None:
        if sum([error is not None, fail, responses is not None]) > 1:
            raise ValueError("FakeLLMManager 的 error / fail / responses 三态互斥，至多设一个")
        self._error = error
        self._fail = fail
        self._fail_error = fail_error
        self._responses = list(responses) if responses is not None else None
        self._response = response
        self._visual = visual
        self._context_windows = dict(context_windows or {})
        self.calls: List[Dict[str, Any]] = []
        self._gate = gate
        self.gate_reached = asyncio.Event()

    # === 三个调用入口（与 LLMManager 对外契约同名同返回）===

    async def generate(self, input: Any, **kwargs: Any) -> Response:
        await self._pass_gate()
        return self._next_reply("generate", input, kwargs)

    async def generate_vision(self, prompt: Any, images: Any = None, **kwargs: Any) -> Response:
        kwargs.pop("images", None)
        await self._pass_gate()
        return self._next_reply("generate_vision", prompt, kwargs)

    async def generate_audio(self, input: Any, **kwargs: Any) -> Response:
        await self._pass_gate()
        return self._next_reply("generate_audio", input, kwargs)

    # === 装配查询出口（duck-type 消费方按需使用）===

    def is_profile_visual(self, profile: Optional[str] = None) -> bool:
        return self._visual

    def get_profile_context_window(self, profile: Optional[str] = None) -> int:
        """按 profile 查可用窗口；替身不做模型窗口配置，统一返回 0（= 未配置）。"""
        return 0

    def get_model_context_window(self, model_identifier: str) -> int:
        return self._context_windows.get(model_identifier, 0)

    def get_model_context_windows(self) -> Dict[str, int]:
        return dict(self._context_windows)

    # === 生命周期（有消费方 setup/cleanup 探测时保持可调）===

    async def setup(self, config: Any) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    # === 内部：闸门与按三态产出应答 ===

    async def _pass_gate(self) -> None:
        if self._gate is None:
            return
        self.gate_reached.set()
        await self._gate.wait()

    def _next_reply(self, method: str, input: Any, kwargs: Dict[str, Any]) -> Response:
        self.calls.append({"method": method, "input": input, **kwargs})
        if self._error is not None:
            raise self._error
        if self._fail:
            return Response(success=False, content=None, error=self._fail_error)
        if self._responses is not None:
            if not self._responses:
                raise AssertionError(
                    f"FakeLLMManager 收到未预期的第 {len(self.calls)} 次 {method} 调用（应答队列已弹尽）"
                )
            return self._responses.pop(0)
        if self._response is not None:
            return self._response
        return Response(success=True, content="fake-llm-reply", model="fake-model", usage=Usage(total_tokens=0))
