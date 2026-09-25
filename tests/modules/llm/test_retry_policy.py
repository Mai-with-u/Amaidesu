"""重试决策分类表测试

fake Client 产出分类异常（Retryable / Fatal / Timeout / Interrupted），
断言 Engine 依分类表驱动的调用次数与 failover / 中止行为：

- Retryable → 同一模型有上限重试（上限 = provider max_retries）
- Fatal → 不重试，直接切 model_list 下一个模型
- Timeout → 切下一个模型
- Interrupted → 整体中止，异常向调用方传播
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from unittest.mock import patch

import pytest

from src.modules.llm.client import BaseLLMClient
from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.engine import LLMManager
from src.modules.llm.errors import FatalError, LLMInterruptedError, LLMTimeoutError, RetryableError
from src.modules.llm.payload import GenerateRequest, Response

RETRY_LIMIT = 3
"""provider 显式 max_retries：Retryable 分类下每模型的调用上限"""


def _policy_config() -> Dict[str, Any]:
    """两个模型挂在同一 provider（max_retries=3、零退避），sequential 起跑"""
    return {
        "llm_providers": [
            {
                "name": "fake",
                "client_type": "policyfake",
                "base_url": "fake://local",
                "api_key": "k",
                "max_retries": RETRY_LIMIT,
                "retry_delay": 0,
            },
        ],
        "llm_models": [
            {"name": "m1", "model_identifier": "fake-1", "api_provider": "fake"},
            {"name": "m2", "model_identifier": "fake-2", "api_provider": "fake"},
        ],
        "llm_profiles": {
            "planner": {
                "model_list": ["m1", "m2"],
                "selection_strategy": {"name": "sequential"},
                "temperature": 0.1,
                "max_tokens": 16,
            },
            "vision": {"model_list": ["m1", "m2"]},
        },
    }


def _make_policy_client(behavior: Callable[[str], Optional[Exception]]) -> type:
    """生成按 model 决定失败形态的 fake 厂商客户端（记录每次收到的 model 标识）

    behavior 返回分类异常即抛出；返回 None 表示该模型成功返回。
    """

    class PolicyFakeClient(BaseLLMClient):
        def __init__(self, config: Dict[str, Any]):
            super().__init__(config)
            self.calls: List[str] = []

        async def generate(
            self,
            request: GenerateRequest,
            *,
            model: str,
            temperature: Any = None,
            on_delta: Any = None,
            interrupt_flag: Any = None,
        ) -> Response:
            self.calls.append(model)
            exc = behavior(model)
            if exc is not None:
                raise exc
            return Response(success=True, content=f"{model} 接管", model=f"{model}-actual")

        async def generate_vision(
            self, request: GenerateRequest, images: List[Any], *, model: str, **_: Any
        ) -> Response:
            self.calls.append(model)
            exc = behavior(model)
            if exc is not None:
                raise exc
            return Response(success=True, content=f"{model} 视觉接管", model=f"{model}-actual")

    return PolicyFakeClient


async def _run_generate(client_cls: type):
    """把 fake 客户端挂进调度表并跑一次 generate，返回 (fake 实例, 响应)"""
    with patch.dict(_CLIENT_DISPATCH, {"policyfake": client_cls}):
        manager = LLMManager()
        await manager.setup(_policy_config())
        fake = manager._provider_clients["fake"]
        response = await manager.generate("你好", profile="planner")
        return fake, response


@pytest.mark.asyncio
async def test_fatal_no_retry():
    """Fatal 分类：不重试，每模型只调 1 次并切到 model_list 下一个模型"""
    client_cls = _make_policy_client(lambda _model: FatalError("请求被服务端拒绝（HTTP 400）"))
    fake, response = await _run_generate(client_cls)

    # m1 首次失败即切换，m2 也只调 1 次；全列表失败走既有失败返回
    assert fake.calls == ["fake-1", "fake-2"]
    assert response.success is False
    assert "全部模型失败" in (response.error or "")


@pytest.mark.asyncio
async def test_retryable_bounded():
    """Retryable 分类：同一模型上有上限重试，耗尽后才切换"""
    client_cls = _make_policy_client(lambda _model: RetryableError("请求过于频繁（429）"))
    fake, response = await _run_generate(client_cls)

    assert fake.calls == ["fake-1"] * RETRY_LIMIT + ["fake-2"] * RETRY_LIMIT
    assert response.success is False


@pytest.mark.asyncio
async def test_timeout_switches_to_next_model():
    """Timeout 分类：不在本模型重试，直接切下一个模型成功返回"""
    client_cls = _make_policy_client(lambda model: LLMTimeoutError("LLM 请求超时") if model == "fake-1" else None)
    fake, response = await _run_generate(client_cls)

    assert fake.calls == ["fake-1", "fake-2"]
    assert response.success is True
    assert response.content == "fake-2 接管"


@pytest.mark.asyncio
async def test_interrupted_aborts_whole_call():
    """Interrupted 分类：整体中止，异常向调用方传播且不再触碰后续模型"""
    client_cls = _make_policy_client(lambda _model: LLMInterruptedError("调用被中断"))

    with patch.dict(_CLIENT_DISPATCH, {"policyfake": client_cls}):
        manager = LLMManager()
        await manager.setup(_policy_config())
        fake = manager._provider_clients["fake"]

        with pytest.raises(LLMInterruptedError):
            await manager.generate("你好", profile="planner")

    assert fake.calls == ["fake-1"]
