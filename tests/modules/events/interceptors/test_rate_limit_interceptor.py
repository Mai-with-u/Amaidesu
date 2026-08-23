"""
RateLimitInterceptor 事件拦截器测试（Wave 5）

由 ``test_rate_limit_pipeline.py`` 改造：从 Pipeline 测试改为 Interceptor 测试。
verbatim 行为：
- 全局消息频率限制
- 用户级消息频率限制
- 滑动窗口算法
- reset() 重置
"""

from __future__ import annotations

import asyncio

from src.modules.events.interceptors.rate_limit import RateLimitInterceptor


def _payload(user_id: str = "u1", text: str = "hello") -> dict:
    """构造测试用事件 payload"""
    return {
        "user_id": user_id,
        "text": text,
        "timestamp_ms": 0,
    }


async def _process_n(interceptor: RateLimitInterceptor, n: int, user_id: str = "u1") -> list:
    """顺序处理 n 条事件，返回每条的拦截结果（payload 或 None）"""
    results = []
    for i in range(n):
        p = _payload(user_id=user_id, text=f"msg_{i}")
        result = await interceptor.intercept("room.message.danmaku", p, source="test")
        results.append(result)
    return results


def test_rate_limit_basic_creation() -> None:
    """基本构造"""
    interceptor = RateLimitInterceptor(global_rate_limit=10, user_rate_limit=3, window_size=60)
    assert interceptor.name == "rate_limit"
    assert interceptor._global_rate_limit == 10
    assert interceptor._user_rate_limit == 3
    assert interceptor._window_size == 60


async def test_rate_limit_pass_through() -> None:
    """第一条消息通过"""
    interceptor = RateLimitInterceptor(global_rate_limit=10, user_rate_limit=3, window_size=60)
    p = _payload()
    result = await interceptor.intercept("room.message.danmaku", p, source="test")
    assert result is not None


async def test_rate_limit_user_limit_triggers() -> None:
    """用户级限流：连续 3 条后第 4 条被丢弃"""
    interceptor = RateLimitInterceptor(global_rate_limit=100, user_rate_limit=3, window_size=60)
    results = await _process_n(interceptor, 4, user_id="u1")
    assert results[0] is not None
    assert results[1] is not None
    assert results[2] is not None
    assert results[3] is None  # 第 4 条被限流


async def test_rate_limit_per_user_independent() -> None:
    """每用户独立计数"""
    interceptor = RateLimitInterceptor(global_rate_limit=100, user_rate_limit=2, window_size=60)
    # u1 第 1、2 条
    assert (await interceptor.intercept("room.message.danmaku", _payload("u1"), "test")) is not None
    assert (await interceptor.intercept("room.message.danmaku", _payload("u1"), "test")) is not None
    # u1 第 3 条 → 限流
    assert (await interceptor.intercept("room.message.danmaku", _payload("u1"), "test")) is None
    # u2 仍可发
    assert (await interceptor.intercept("room.message.danmaku", _payload("u2"), "test")) is not None


async def test_rate_limit_global_limit() -> None:
    """全局限流"""
    interceptor = RateLimitInterceptor(global_rate_limit=2, user_rate_limit=100, window_size=60)
    # 前两条通过（来自不同用户）
    assert (await interceptor.intercept("room.message.danmaku", _payload("u1"), "test")) is not None
    assert (await interceptor.intercept("room.message.danmaku", _payload("u2"), "test")) is not None
    # 第 3 条触发全局限流
    assert (await interceptor.intercept("room.message.danmaku", _payload("u3"), "test")) is None


async def test_rate_limit_reset() -> None:
    """reset() 重置计数器"""
    interceptor = RateLimitInterceptor(global_rate_limit=1, user_rate_limit=1, window_size=60)
    await interceptor.intercept("room.message.danmaku", _payload("u1"), "test")
    # 第 2 条限流
    assert (await interceptor.intercept("room.message.danmaku", _payload("u1"), "test")) is None
    # 重置
    await interceptor.reset()
    # 重置后能再发
    assert (await interceptor.intercept("room.message.danmaku", _payload("u1"), "test")) is not None


def test_rate_limit_extract_user_id_fallback() -> None:
    """无 user_id 时回退到 unknown_user"""
    interceptor = RateLimitInterceptor()
    assert interceptor._extract_user_id({}) == "unknown_user"
    assert interceptor._extract_user_id({"text": "hi"}) == "unknown_user"
    assert interceptor._extract_user_id({"user_id": "alice"}) == "alice"
    assert interceptor._extract_user_id({"open_id": "bob"}) == "bob"
