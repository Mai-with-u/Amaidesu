"""
ProfanityFilterOutputPipeline 测试
"""

import pytest

from src.domains.output.parameters.render_parameters import ExpressionParameters
from src.domains.output.pipelines.profanity_filter.pipeline import ProfanityFilterOutputPipeline


@pytest.mark.asyncio
async def test_profanity_filter_basic():
    """测试基本的脏话过滤功能"""
    config = {
        "enabled": True,
        "priority": 100,
        "words": ["badword", "test"],
        "replacement": "***",
        "filter_tts": True,
        "filter_subtitle": True,
        "case_sensitive": False,
        "drop_on_match": False,
    }

    pipeline = ProfanityFilterOutputPipeline(config)

    params = ExpressionParameters()
    params.tts_text = "This is a badword example"
    params.subtitle_text = "Subtitle with test content"

    result = await pipeline.process(params)

    assert result is not None
    assert "badword" not in result.tts_text
    assert "test" not in result.subtitle_text
    assert "***" in result.tts_text
    assert "***" in result.subtitle_text


@pytest.mark.asyncio
async def test_profanity_filter_chinese():
    """测试中文脏话过滤"""
    config = {
        "enabled": True,
        "priority": 100,
        "words": ["脏话", "敏感词"],
        "replacement": "【已过滤】",
        "filter_tts": True,
        "filter_subtitle": True,
        "case_sensitive": False,
        "drop_on_match": False,
    }

    pipeline = ProfanityFilterOutputPipeline(config)

    params = ExpressionParameters()
    params.tts_text = "这是一段包含脏话的文本"
    params.subtitle_text = "字幕中也有敏感词"

    result = await pipeline.process(params)

    assert result is not None
    assert "脏话" not in result.tts_text
    assert "敏感词" not in result.subtitle_text
    assert "【已过滤】" in result.tts_text
    assert "【已过滤】" in result.subtitle_text


@pytest.mark.asyncio
async def test_profanity_filter_drop_on_match():
    """测试匹配时丢弃消息"""
    config = {
        "enabled": True,
        "priority": 100,
        "words": ["blocked"],
        "replacement": "***",
        "filter_tts": True,
        "filter_subtitle": True,
        "case_sensitive": False,
        "drop_on_match": True,
    }

    pipeline = ProfanityFilterOutputPipeline(config)

    params = ExpressionParameters()
    params.tts_text = "This message is blocked"

    result = await pipeline.process(params)

    assert result is None  # 消息被丢弃
