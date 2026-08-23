"""
SimilarFilterInterceptor 事件拦截器测试（Wave 5）

由 ``test_similar_filter_pipeline.py`` 改造：从 Pipeline 测试改为 Interceptor 测试。
verbatim 行为：
- 相似度算法（difflib.SequenceMatcher + 包含关系加权）
- 跨用户过滤策略
- 时间窗口缓存
- min_text_length 跳过过滤
- reset() 重置
"""

from __future__ import annotations

import asyncio

from src.modules.events.interceptors.similar_filter import SimilarFilterInterceptor


def _payload(text: str, user_id: str = "u1") -> dict:
    return {
        "user_id": user_id,
        "text": text,
        "timestamp_ms": 0,
    }


async def _send(interceptor: SimilarFilterInterceptor, text: str, user_id: str = "u1") -> dict | None:
    return await interceptor.intercept("room.message.danmaku", _payload(text, user_id), source="test_group")


def test_similar_filter_basic_creation() -> None:
    """基本构造"""
    interceptor = SimilarFilterInterceptor(
        similarity_threshold=0.85,
        time_window=5.0,
        min_text_length=3,
        cross_user_filter=True,
    )
    assert interceptor.name == "similar_filter"
    assert interceptor._similarity_threshold == 0.85
    assert interceptor._time_window == 5.0
    assert interceptor._min_text_length == 3
    assert interceptor._cross_user_filter is True


async def test_similar_filter_first_message_passes() -> None:
    """第一条消息通过"""
    interceptor = SimilarFilterInterceptor()
    result = await _send(interceptor, "这是一条测试消息")
    assert result is not None


async def test_similar_filter_similar_message_filtered() -> None:
    """相似消息被过滤（丢弃）"""
    interceptor = SimilarFilterInterceptor()
    text = "这是一条测试消息"
    result1 = await _send(interceptor, text)
    assert result1 is not None
    # 完全相同的第二条被过滤
    result2 = await _send(interceptor, text)
    assert result2 is None


async def test_similar_filter_different_message_passes() -> None:
    """不同消息通过"""
    interceptor = SimilarFilterInterceptor()
    assert (await _send(interceptor, "消息 A 是这样")) is not None
    assert (await _send(interceptor, "消息 B 是那样")) is not None


async def test_similar_filter_short_message_skipped() -> None:
    """短于 min_text_length 的文本跳过过滤直接放行"""
    interceptor = SimilarFilterInterceptor(min_text_length=3)
    # "12" 长度 2 < 3，跳过过滤
    assert (await _send(interceptor, "12")) is not None


async def test_similar_filter_cross_user_enabled() -> None:
    """跨用户过滤启用：不同用户的相似消息被过滤"""
    interceptor = SimilarFilterInterceptor(cross_user_filter=True)
    text = "相同消息内容"
    assert (await _send(interceptor, text, user_id="u1")) is not None
    # u2 相同文本被过滤
    assert (await _send(interceptor, text, user_id="u2")) is None


async def test_similar_filter_cross_user_disabled() -> None:
    """跨用户过滤禁用：不同用户的相似消息各自放行"""
    interceptor = SimilarFilterInterceptor(cross_user_filter=False)
    text = "相同消息内容"
    assert (await _send(interceptor, text, user_id="u1")) is not None
    # u2 相同文本不被过滤
    assert (await _send(interceptor, text, user_id="u2")) is not None


async def test_similar_filter_different_sources_isolated() -> None:
    """不同 source（group_id）独立过滤"""
    interceptor = SimilarFilterInterceptor()
    text = "相同的消息"
    assert (await interceptor.intercept("room.message.danmaku", _payload(text, "u1"), source="g1")) is not None
    # 不同 group_id（source）独立缓存
    assert (await interceptor.intercept("room.message.danmaku", _payload(text, "u1"), source="g2")) is not None


async def test_similar_filter_calculate_similarity_contains() -> None:
    """包含关系相似度加权：'666' vs '6666' 相似度应 >= 0.5"""
    interceptor = SimilarFilterInterceptor()
    sim = interceptor._calculate_similarity("666", "6666")
    # contained_similarity = 3/4 = 0.75
    assert sim >= 0.5


async def test_similar_filter_reset_clears_cache() -> None:
    """reset() 清空缓存"""
    interceptor = SimilarFilterInterceptor()
    text = "测试消息"
    await _send(interceptor, text)
    # 缓存非空
    assert interceptor._text_cache.get("test_group")
    await interceptor.reset()
    # 缓存清空
    assert not interceptor._text_cache
    # 相同消息再次发送 → 通过
    assert (await _send(interceptor, text)) is not None
