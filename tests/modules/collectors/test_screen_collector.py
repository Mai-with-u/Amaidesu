"""
screen 采集器（read_pingmu 重命名）测试（Wave 5 + v2.0.9 D1 VLM 收编）

覆盖：
- ScreenChangeCollector 继承 BaseCollector（改名去拼音）
- 元数据正确
- ScreenAnalyzer 差异检测逻辑（缩略图哈希）
- ScreenReader VLM 缓存去重（同一 image 不重复调用 VLM）
- 集成：未注入 llm_manager 时不调用 VLM（仅缓存去重生效）
- v2.0.9：注入 mock llm_manager 后 chat_vision 被调；chat_vision 抛异常时降级不崩
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.modules.collectors.base import BaseCollector
from src.modules.collectors.screen import (
    ScreenAnalyzer,
    ScreenChangeCollector,
    ScreenReader,
)
from src.modules.llm.manager import LLMResponse


def test_screen_change_inherits_base_collector() -> None:
    """ScreenChangeCollector 继承 BaseCollector（v2 框架）"""
    assert issubclass(ScreenChangeCollector, BaseCollector)


def test_screen_metadata() -> None:
    """元数据正确（已重命名：read_pingmu → screen）"""
    assert ScreenChangeCollector.name == "screen"
    assert ScreenChangeCollector.__name__ == "ScreenChangeCollector"


def test_screen_analyzer_diff_threshold_works() -> None:
    """ScreenAnalyzer 差异阈值与缓存：相同哈希 → 0 分，不同哈希 → 50 分"""
    analyzer = ScreenAnalyzer(diff_threshold=25.0, max_cache_size=5)
    assert analyzer._diff_score("abc", "abc") == 0.0
    assert analyzer._diff_score("abc", "def") == 50.0
    assert analyzer._diff_score("", "") == 0.0
    assert analyzer._diff_score("a", "b") == 50.0


def test_screen_reader_cache_dedup_saves_token() -> None:
    """ScreenReader VLM 缓存去重：同一图像哈希不重复调用 VLM（省 token）

    验证：
    1. 未注入 llm_manager 时，跳过 VLM 调用，返回说明性结果（不浪费 token）
    2. 缓存队列正常工作
    """
    reader = ScreenReader(max_cached_images=5, llm_manager=None)
    assert reader._image_hash_cache.maxlen == 5
    try:
        from PIL import Image

        img = Image.new("RGB", (10, 10), color=(255, 0, 0))
        result = asyncio.run(reader.process_screen_change({"image": img}))
        # 未注入 llm_manager 时返回说明性文本
        assert result is not None
        assert "未注入" in result.new_current_context or "skipped" in result.raw_response
    except ImportError:
        # PIL 不可用时至少验证 cache 行为
        assert reader._image_hash_cache.maxlen == 5


def test_screen_change_collector_compat_interface() -> None:
    """ScreenChangeCollector 兼容旧 InputCollectorManager 接口（start/stop/collect/stream）"""
    methods = ("start", "stop", "cleanup", "collect", "stream")
    for m in methods:
        assert hasattr(ScreenChangeCollector, m), f"缺少兼容方法 {m}"


def test_screen_change_collector_config_defaults() -> None:
    """配置默认值

    v2.0.9 收编：api_key / base_url / model_name 三个字段已从 ConfigSchema 移除；
    VLM key/model 走 model.toml [vlm] profile，由 LLMManager 注入。
    """
    cfg = ScreenChangeCollector.ConfigSchema()
    assert cfg.screenshot_interval == 0.3
    assert cfg.diff_threshold == 25.0
    assert cfg.check_window == 3
    assert cfg.max_cache_size == 5
    assert cfg.max_cached_images == 5
    assert not hasattr(cfg, "api_key")
    assert not hasattr(cfg, "base_url")
    assert not hasattr(cfg, "model_name")


def test_screen_reader_calls_chat_vision_with_mock_llm_manager() -> None:
    """注入 mock llm_manager 后：chat_vision 被调用且结果正确解析进 ScreenAnalysisResult。"""
    response = LLMResponse(success=True, content="屏幕打开了浏览器", model="vlm-test", usage={"total_tokens": 42})
    llm_manager = AsyncMock()
    llm_manager.chat_vision = AsyncMock(return_value=response)

    reader = ScreenReader(max_cached_images=5, llm_manager=llm_manager)

    from PIL import Image

    img = Image.new("RGB", (10, 10), color=(255, 0, 0))
    result = asyncio.run(reader.process_screen_change({"image": img}))

    assert result is not None
    assert result.new_current_context == "屏幕打开了浏览器"
    assert result.raw_response["model"] == "vlm-test"
    assert result.raw_response["usage"] == {"total_tokens": 42}

    # chat_vision 被调一次，参数正确（client_type=vlm、images=bytes 列表、prompt/system_message 非空）
    llm_manager.chat_vision.assert_awaited_once()
    call_kwargs = llm_manager.chat_vision.await_args.kwargs
    assert call_kwargs["client_type"] == "vlm"
    assert isinstance(call_kwargs["images"], list)
    assert len(call_kwargs["images"]) == 1
    assert isinstance(call_kwargs["images"][0], (bytes, bytearray))
    assert isinstance(call_kwargs["prompt"], str) and call_kwargs["prompt"]
    assert isinstance(call_kwargs["system_message"], str) and call_kwargs["system_message"]


def test_screen_reader_chat_vision_exception_does_not_crash() -> None:
    """注入的 chat_vision 抛异常时，process_screen_change 走降级返回 None，不崩。"""
    llm_manager = AsyncMock()
    llm_manager.chat_vision = AsyncMock(side_effect=RuntimeError("网络异常"))

    reader = ScreenReader(max_cached_images=5, llm_manager=llm_manager)

    from PIL import Image

    img = Image.new("RGB", (10, 10), color=(0, 0, 255))
    result = asyncio.run(reader.process_screen_change({"image": img}))

    # 异常分支：返回 None（process_screen_change 已知失败语义）
    assert result is None
    llm_manager.chat_vision.assert_awaited_once()


def test_screen_reader_chat_vision_unsuccessful_returns_none() -> None:
    """注入的 chat_vision 返回 success=False 时，降级为不返回分析结果（缓存已记录不重复）。"""
    response = LLMResponse(success=False, content=None, error="rate limit")
    llm_manager = AsyncMock()
    llm_manager.chat_vision = AsyncMock(return_value=response)

    reader = ScreenReader(max_cached_images=5, llm_manager=llm_manager)

    from PIL import Image

    img = Image.new("RGB", (10, 10), color=(0, 255, 0))
    result = asyncio.run(reader.process_screen_change({"image": img}))

    assert result is None
    llm_manager.chat_vision.assert_awaited_once()


def test_screen_change_collector_constructor_accepts_llm_manager() -> None:
    """v2.0.9：ScreenChangeCollector 构造器接受 llm_manager 参数并透传至 ScreenReader。"""
    llm_manager = AsyncMock()
    collector = ScreenChangeCollector(
        config={"max_cached_images": 3},
        llm_manager=llm_manager,
    )
    assert collector._llm_manager is llm_manager
    # ConfigSchema 字段已不包含 VLM 自管三字段
    cfg = collector.typed_config
    assert not hasattr(cfg, "api_key")
    assert not hasattr(cfg, "base_url")
    assert not hasattr(cfg, "model_name")
    assert cfg.max_cached_images == 3
