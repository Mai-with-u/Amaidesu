"""
ProfanityFilterPipeline 测试
"""

import pytest

from src.domains.output.pipelines.profanity_filter.pipeline import ProfanityFilterPipeline
from src.modules.types import Intent


@pytest.mark.asyncio
async def test_profanity_filter_basic():
    """测试基本的脏话过滤功能"""
    config = {
        "enabled": True,
        "priority": 100,
        "words": ["badword", "test"],
        "replacement": "***",
        "case_sensitive": False,
        "drop_on_match": False,
    }

    pipeline = ProfanityFilterPipeline(config)

    intent = Intent(
        original_text="用户输入",
        response_text="This is a badword example",
    )

    result = await pipeline.process(intent)

    assert result is not None
    assert "badword" not in result.response_text
    assert "***" in result.response_text


@pytest.mark.asyncio
async def test_profanity_filter_chinese():
    """测试中文脏话过滤"""
    config = {
        "enabled": True,
        "priority": 100,
        "words": ["脏话", "敏感词"],
        "replacement": "【已过滤】",
        "case_sensitive": False,
        "drop_on_match": False,
    }

    pipeline = ProfanityFilterPipeline(config)

    intent = Intent(
        original_text="用户输入",
        response_text="这是一段包含脏话和敏感词的文本",
    )

    result = await pipeline.process(intent)

    assert result is not None
    assert "脏话" not in result.response_text
    assert "敏感词" not in result.response_text
    assert "【已过滤】" in result.response_text


@pytest.mark.asyncio
async def test_profanity_filter_drop_on_match():
    """测试匹配时丢弃消息"""
    config = {
        "enabled": True,
        "priority": 100,
        "words": ["blocked"],
        "replacement": "***",
        "case_sensitive": False,
        "drop_on_match": True,
    }

    pipeline = ProfanityFilterPipeline(config)

    intent = Intent(
        original_text="用户输入",
        response_text="This message is blocked",
    )

    result = await pipeline.process(intent)

    assert result is None  # 消息被丢弃


@pytest.mark.asyncio
async def test_profanity_filter_case_insensitive():
    """测试不区分大小写过滤"""
    config = {
        "enabled": True,
        "priority": 100,
        "words": ["badword"],
        "replacement": "***",
        "case_sensitive": False,
        "drop_on_match": False,
    }

    pipeline = ProfanityFilterPipeline(config)

    intent = Intent(
        original_text="用户输入",
        response_text="This has BADWORD and BadWord",
    )

    result = await pipeline.process(intent)

    assert result is not None
    assert "BADWORD" not in result.response_text
    assert "BadWord" not in result.response_text
    assert result.response_text.count("***") == 2


@pytest.mark.asyncio
async def test_profanity_filter_case_sensitive():
    """测试区分大小写过滤"""
    config = {
        "enabled": True,
        "priority": 100,
        "words": ["badword"],
        "replacement": "***",
        "case_sensitive": True,
        "drop_on_match": False,
    }

    pipeline = ProfanityFilterPipeline(config)

    intent = Intent(
        original_text="用户输入",
        response_text="This has BADWORD and badword",
    )

    result = await pipeline.process(intent)

    assert result is not None
    assert "BADWORD" in result.response_text  # 大写版本不受影响
    assert "badword" not in result.response_text  # 只过滤小写版本


@pytest.mark.asyncio
async def test_profanity_filter_no_match():
    """测试无匹配时不修改文本"""
    config = {
        "enabled": True,
        "priority": 100,
        "words": ["xxx"],
        "replacement": "***",
        "case_sensitive": False,
        "drop_on_match": False,
    }

    pipeline = ProfanityFilterPipeline(config)

    original_text = "这是一段正常的文本"
    intent = Intent(
        original_text="用户输入",
        response_text=original_text,
    )

    result = await pipeline.process(intent)

    assert result is not None
    assert result.response_text == original_text
