"""流式增量直通：增量在整次调用结束前就到达调用方，Engine 不缓冲。

替身客户端吐出第一个增量后，等"调用方已收到"信号才完成调用；任何缓冲实现下信号永远等不到。
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from src.modules.llm.client import BaseLLMClient
from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.engine import LLMManager
from src.modules.llm.payload import GenerateRequest, Response


async def test_on_delta_fires_before_call_completes() -> None:
    received_event = asyncio.Event()
    received: list[str] = []

    class TypewriterFakeClient(BaseLLMClient):
        async def generate(self, request: GenerateRequest, *, model: str, on_delta: Any = None, **kw: Any) -> Response:
            on_delta("content", "第一个增量")
            await asyncio.wait_for(received_event.wait(), timeout=5.0)
            return Response(success=True, content="完整回复", model=model)

    def consumer(kind: str, text: str) -> None:
        received.append(text)
        received_event.set()

    config = {
        "llm_providers": [{"name": "fake", "client_type": "typewriter", "max_retries": 0}],
        "llm_models": [{"name": "m1", "model_identifier": "fake-1", "api_provider": "fake"}],
        "llm_profiles": {"planner": {"model_list": ["m1"]}},
    }
    with patch.dict(_CLIENT_DISPATCH, {"typewriter": TypewriterFakeClient}):
        with patch("src.modules.llm.engine.record_call_outcome", new_callable=AsyncMock):
            manager = LLMManager(llm_repo=object())
            await manager.setup(config)
            response = await manager.generate("你好", profile="planner", on_delta=consumer)

    assert response.success is True and received == ["第一个增量"]
