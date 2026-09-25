"""请求生命周期测试：覆盖调用等待、主动取消与故障切换。"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import loguru
import pytest

from src.modules.llm.client import BaseLLMClient
from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.engine import LLMManager
from src.modules.llm.errors import LLMInterruptedError, RetryableError
from src.modules.llm.payload import GenerateRequest, Response, Usage

loguru_logger = loguru.logger


def _timeout_config(hard_timeout_ms: int, *, retry_delay: float = 0.0) -> Dict[str, Any]:
    """两模型 sequential 配置：m1 走可编程行为，m2 立即成功"""
    return {
        "llm_providers": [
            {
                "name": "fake",
                "client_type": "timeoutfake",
                "base_url": "fake://local",
                "api_key": "k",
                "max_retries": 3,
                "retry_delay": retry_delay,
                "timeout": 60,
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
                "hard_timeout_ms": hard_timeout_ms,
                "temperature": 0.1,
                "max_tokens": 16,
            },
            "vision": {"model_list": ["m1", "m2"], "hard_timeout_ms": hard_timeout_ms},
        },
    }


def _make_timeout_client(m1_behavior: Any) -> type:
    """生成 m1 行为可编程的 fake 厂商客户端（m2 恒成功），记录每次收到的 model 标识

    ``m1_behavior`` 是 async 可调用：await 正常结束视为成功返回；抛异常即按
    分类异常路径处理。on_delta 作为参数传入供流式场景吐 token。
    """

    class TimeoutFakeClient(BaseLLMClient):
        def __init__(self, config: Dict[str, Any]):
            super().__init__(config)
            self.calls: List[str] = []

        async def generate(
            self,
            request: GenerateRequest,
            *,
            model: str,
            temperature: Any = None,
            max_tokens: Any = None,
            on_delta: Any = None,
            interrupt_flag: Any = None,
        ) -> Response:
            self.calls.append(model)
            if model == "fake-1":
                await m1_behavior(on_delta)
            return Response(
                success=True,
                content=f"{model} 接管",
                model=f"{model}-actual",
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

        async def generate_vision(
            self, request: GenerateRequest, images: List[Any], *, model: str, **_: Any
        ) -> Response:
            self.calls.append(model)
            return Response(success=True, content=f"{model} 视觉接管", model=f"{model}-actual")

    return TimeoutFakeClient


async def _run(client_cls: type, config: Dict[str, Any], **generate_kwargs: Any):
    """装配 LLMManager 跑一次 generate，返回 (fake 实例, 响应, record_usage mock)"""
    with patch.dict(_CLIENT_DISPATCH, {"timeoutfake": client_cls}):
        with patch("src.modules.llm.engine.record_usage", new_callable=AsyncMock) as record_mock:
            manager = LLMManager(llm_repo=object())  # 注入 repo 使 llm_usage 落库路径生效
            await manager.setup(config)
            fake = manager._provider_clients["fake"]
            response = await manager.generate("你好", profile="planner", **generate_kwargs)
            return fake, response, record_mock


async def _noop_behavior(_on_delta: Any) -> None:
    """m1 立即成功的默认行为（启动期校验测试不关心调用结果）"""


@pytest.mark.asyncio
async def test_timeout_cancels_and_fails_over():
    """m1 睡 5s、墙 2000ms：到点取消并切 m2，总耗时贴合墙，usage 只记成功的 m2"""

    async def m1_sleep(_on_delta: Any) -> None:
        await asyncio.sleep(5)

    client_cls = _make_timeout_client(m1_sleep)
    config = _timeout_config(2000)

    start = time.perf_counter()
    fake, response, record_mock = await _run(client_cls, config)
    elapsed_ms = (time.perf_counter() - start) * 1000

    # 墙到点即切：总耗时贴合 2000ms（±200ms 容差），远小于 m1 的 5s 睡眠
    assert 1800 <= elapsed_ms <= 2400, f"总耗时 {elapsed_ms:.0f}ms 偏离硬超时墙"
    assert fake.calls == ["fake-1", "fake-2"]
    assert response.success is True
    assert response.content == "fake-2 接管"
    # llm_usage 落库口径：超时失败的 m1 无记录，成功的 m2 恰好一条
    assert record_mock.await_count == 1
    assert record_mock.await_args.kwargs["model_name"] == "fake-2-actual"


@pytest.mark.asyncio
async def test_stream_timeout_after_first_token_aborts():
    """首 token 后卡到墙：调用方已收到的增量不追溯、不 failover、整体中止"""

    async def m1_stream_then_hang(on_delta: Any) -> None:
        on_delta("content", "首段")
        await asyncio.sleep(30)

    client_cls = _make_timeout_client(m1_stream_then_hang)
    received: List[str] = []

    with pytest.raises(LLMInterruptedError):
        await _run(client_cls, _timeout_config(500), on_delta=lambda kind, text: received.append(text))

    # 中止语义：增量直通已外发、不追溯；后续模型未被触碰（不 failover）
    assert received == ["首段"]


@pytest.mark.asyncio
async def test_stream_timeout_before_first_token_fails_over():
    """首 token 前卡到墙：m1 标记失败切 m2 成功返回"""

    async def m1_hang_before_first_token(on_delta: Any) -> None:
        await asyncio.sleep(30)

    client_cls = _make_timeout_client(m1_hang_before_first_token)
    received: List[str] = []

    fake, response, _record = await _run(
        client_cls,
        _timeout_config(500),
        on_delta=lambda kind, text: received.append(text),
    )

    assert fake.calls == ["fake-1", "fake-2"]
    assert response.success is True
    assert response.content == "fake-2 接管"
    # m2 成功且无增量调用；m1 首 token 前失败、增量未外发
    assert received == []


@pytest.mark.asyncio
async def test_retry_budget_truncated_by_wall():
    """墙内 Retryable 重试被截断：首败后退避中到墙，m1 只调一次即切 m2"""

    async def m1_rate_limited(_on_delta: Any) -> None:
        raise RetryableError("请求过于频繁（429）")

    client_cls = _make_timeout_client(m1_rate_limited)
    # retry_delay=1s：首败即失败，退避睡眠中撞上 300ms 的墙
    config = _timeout_config(300, retry_delay=1.0)

    fake, response, _record = await _run(client_cls, config)

    assert fake.calls == ["fake-1", "fake-2"]
    assert response.success is True
    assert response.content == "fake-2 接管"


@pytest.mark.asyncio
async def test_setup_warns_when_hard_timeout_below_provider_timeout():
    """启动期弱校验：hard_timeout_ms < provider.timeout → 告警日志（不硬错）"""
    client_cls = _make_timeout_client(_noop_behavior)
    config = _timeout_config(500)
    # provider timeout=2s，profile 墙 500ms：墙先到点，请求级超时成死配置
    config["llm_providers"][0]["timeout"] = 2

    warnings: List[str] = []
    handler_id = loguru_logger.add(lambda msg: warnings.append(str(msg)), level="WARNING")
    try:
        with patch.dict(_CLIENT_DISPATCH, {"timeoutfake": client_cls}):
            manager = LLMManager()
            await manager.setup(config)
    finally:
        loguru_logger.remove(handler_id)

    assert any("hard_timeout_ms=500" in w and "配置告警" in w for w in warnings)


@pytest.mark.asyncio
async def test_setup_no_warning_when_hard_timeout_above_provider_timeout():
    """配置协调时不告警：反向确认弱校验不误报"""
    client_cls = _make_timeout_client(_noop_behavior)
    config = _timeout_config(30_000)
    config["llm_providers"][0]["timeout"] = 10

    warnings: List[str] = []
    handler_id = loguru_logger.add(lambda msg: warnings.append(str(msg)), level="WARNING")
    try:
        with patch.dict(_CLIENT_DISPATCH, {"timeoutfake": client_cls}):
            manager = LLMManager()
            await manager.setup(config)
    finally:
        loguru_logger.remove(handler_id)

    assert not any("配置告警" in w for w in warnings)
