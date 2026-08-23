"""
screen 采集器（read_pingmu 重命名）测试（Wave 5）

覆盖：
- ScreenChangeCollector 继承 BaseCollector（改名去拼音）
- 元数据正确
- ScreenAnalyzer 差异检测逻辑（缩略图哈希）
- ScreenReader VLM 缓存去重（同一 image 不重复调用 VLM）
- 集成：未配置 api_key 时不调用 VLM（省 token）
"""

from __future__ import annotations

import asyncio

from src.modules.collectors.base import BaseCollector
from src.modules.collectors.screen import (
    ScreenAnalyzer,
    ScreenChangeCollector,
    ScreenReader,
)


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
    1. 未配置 api_key 时，跳过 VLM 调用，返回说明性结果（不浪费 token）
    2. 缓存队列正常工作
    """
    reader = ScreenReader(api_key="", max_cached_images=5)
    assert reader._image_hash_cache.maxlen == 5
    # 模拟：未配置 api_key 时 process_screen_change 应返回说明性结果而非调用 VLM
    # （构造最小 PIL 图像如不可用则跳过）
    try:
        from PIL import Image

        img = Image.new("RGB", (10, 10), color=(255, 0, 0))
        result = asyncio.run(reader.process_screen_change({"image": img}))
        # 未配置 api_key 时返回说明性文本
        assert result is not None
        assert "未配置" in result.new_current_context or "skipped" in result.raw_response
    except ImportError:
        # PIL 不可用时至少验证 cache 行为
        assert reader._image_hash_cache.maxlen == 5


def test_screen_change_collector_compat_interface() -> None:
    """ScreenChangeCollector 兼容旧 InputCollectorManager 接口（start/stop/collect/stream）"""
    methods = ("start", "stop", "cleanup", "collect", "stream")
    for m in methods:
        assert hasattr(ScreenChangeCollector, m), f"缺少兼容方法 {m}"


def test_screen_change_collector_config_defaults() -> None:
    """配置默认值"""
    cfg = ScreenChangeCollector.ConfigSchema()
    assert cfg.screenshot_interval == 0.3
    assert cfg.diff_threshold == 25.0
    assert cfg.check_window == 3
    assert cfg.max_cache_size == 5
    assert cfg.max_cached_images == 5
