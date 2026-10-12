"""流式增量直通（打字机效果）测试

证明一件事：流式增量在整次 LLM 调用结束**之前**就到达调用方——
on_delta 收到增量立即转调消费方，Engine 侧不做缓冲。

用事件同步严格证明：fake Client 吐出第一个增量后，等待"调用方已收到"
的 ``asyncio.Event`` 置位才继续完成调用。直通实现下 Event 会置位、
调用正常完成；任何缓冲实现下 Event 永远等不到（等待超时，测试失败）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from src.modules.llm.client import BaseLLMClient
from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.engine import LLMManager
from src.modules.llm.payload import GenerateRequest, Response, Usage

STREAM_DIRECT_WAIT_S = 5.0


def _make_typewriter_client(received_event: asyncio.Event) -> type:
    """吐一个增量后阻塞等待"调用方已收到"信号（received_event）再完成的 fake 客户端"""

    class TypewriterFakeClient(BaseLLMClient):
        def __init__(self, config: Dict[str, Any]):
            super().__init__(config)

        async def generate(
            self,
            request: GenerateRequest,
            *,
            model: str,
            temperature: Any = None,
            reasoning_effort: Optional[str] = None,
            on_delta: Any = None,
            interrupt_flag: Any = None,
        ) -> Response:
            on_delta("content", "第一个增量")
            # 直通实现下调用方已收到增量、Event 已置位；缓冲实现下这里永远等不到
            await asyncio.wait_for(received_event.wait(), timeout=STREAM_DIRECT_WAIT_S)
            return Response(
                success=True,
                content="完整回复",
                model=f"{model}-actual",
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

    return TypewriterFakeClient


def _config() -> Dict[str, Any]:
    return {
        "llm_providers": [
            {
                "name": "fake",
                "client_type": "typewriter",
                "base_url": "fake://local",
                "api_key": "k",
                "max_retries": 0,
                "timeout": 60,
            },
        ],
        "llm_models": [{"name": "m1", "model_identifier": "fake-1", "api_provider": "fake"}],
        "llm_profiles": {
            "planner": {
                "model_list": ["m1"],
                "hard_timeout_ms": 30_000,
                "temperature": 0.1,
                "max_tokens": 16,
            },
        },
    }


@pytest.mark.asyncio
async def test_on_delta_fires_before_call_completes():
    """增量在调用完成前即到达调用方（打字机效果恢复）"""

    received_event = asyncio.Event()
    received: List[str] = []

    def consumer(kind: str, text: str) -> None:
        received.append(text)
        received_event.set()

    client_cls = _make_typewriter_client(received_event)

    with patch.dict(_CLIENT_DISPATCH, {"typewriter": client_cls}):
        with patch("src.modules.llm.engine.record_usage", new_callable=AsyncMock):
            manager = LLMManager(llm_repo=object())
            await manager.setup(_config())
            response = await manager.generate("你好", profile="planner", on_delta=consumer)

    # fake 内部的等待已通过：增量在调用完成前就被回调
    assert received_event.is_set()
    assert received == ["第一个增量"]
    assert response.success is True
