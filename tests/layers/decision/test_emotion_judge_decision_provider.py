"""
EmotionJudgeDecisionProvider 测试
"""

import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from src.providers.emotion_judge import EmotionJudgeDecisionProvider


@pytest.fixture
def emotion_config():
    """EmotionJudge配置"""
    return {
        "api_key": "test_api_key",
        "base_url": "https://test.api.com/v1/",
        "cool_down_seconds": 5,
        "model": {
            "name": "test-model",
            "max_tokens": 10,
            "temperature": 0.3,
        }
    }


@pytest.fixture
def mock_event_bus():
    """Mock EventBus"""
    event_bus = MagicMock()
    event_bus.on = MagicMock()
    event_bus.emit = AsyncMock()
    return event_bus


class TestEmotionJudgeDecisionProvider:
    """测试 EmotionJudgeDecisionProvider"""

    def test_init_with_api_key(self, emotion_config):
        """测试有API key的初始化"""
        with patch('src.providers.emotion_judge.emotion_judge_decision_provider.AsyncOpenAI'):
            provider = EmotionJudgeDecisionProvider(emotion_config)

            assert provider.api_key == "test_api_key"
            assert provider.base_url == "https://test.api.com/v1/"
            assert provider.cool_down_seconds == 5
            assert provider.client is not None

    def test_init_without_api_key(self):
        """测试没有API key的初始化"""
        config = {
            "base_url": "https://test.api.com/v1/",
        }

        provider = EmotionJudgeDecisionProvider(config)

        assert provider.api_key == ""
        assert provider.client is None

    def test_init_with_default_config(self):
        """测试默认配置"""
        config = {}

        with patch('src.providers.emotion_judge.emotion_judge_decision_provider.AsyncOpenAI'):
            provider = EmotionJudgeDecisionProvider(config)

            assert provider.base_url == "https://api.siliconflow.cn/v1/"
            assert provider.cool_down_seconds == 10

    @pytest.mark.asyncio
    async def test_setup_internal(self, emotion_config, mock_event_bus):
        """测试内部设置"""
        with patch('src.providers.emotion_judge.emotion_judge_decision_provider.AsyncOpenAI'):
            provider = EmotionJudgeDecisionProvider(emotion_config)
            await provider.setup(mock_event_bus)

            # 验证事件监听器已注册
            mock_event_bus.on.assert_called_once_with(
                "canonical.message",
                provider._handle_canonical_message,
                priority=100
            )

    @pytest.mark.asyncio
    async def test_decide_returns_none(self, emotion_config):
        """测试decide方法返回None"""
        with patch('src.providers.emotion_judge.emotion_judge_decision_provider.AsyncOpenAI'):
            provider = EmotionJudgeDecisionProvider(emotion_config)

            # 创建一个mock canonical message
            canonical_message = MagicMock()

            result = await provider.decide(canonical_message)

            # decide方法应该返回None
            assert result is None

    def test_extract_text_from_string(self, emotion_config):
        """测试从字符串提取文本"""
        with patch('src.providers.emotion_judge.emotion_judge_decision_provider.AsyncOpenAI'):
            provider = EmotionJudgeDecisionProvider(emotion_config)

            text = provider._extract_text("测试文本")
            assert text == "测试文本"

    def test_extract_text_from_object_with_text_attr(self, emotion_config):
        """测试从有text属性的对象提取文本"""
        with patch('src.providers.emotion_judge.emotion_judge_decision_provider.AsyncOpenAI'):
            provider = EmotionJudgeDecisionProvider(emotion_config)

            obj = MagicMock()
            obj.text = "对象中的文本"

            text = provider._extract_text(obj)
            assert text == "对象中的文本"

    def test_extract_text_from_object_with_content_attr(self, emotion_config):
        """测试从有content属性的对象提取文本"""
        with patch('src.providers.emotion_judge.emotion_judge_decision_provider.AsyncOpenAI'):
            provider = EmotionJudgeDecisionProvider(emotion_config)

            obj = MagicMock()
            # 当text属性不存在或为None时，才检查content属性
            # 所以这里不设置text属性，让它去检查content

            # 模拟hasattr返回False（没有text属性）
            del obj.text
            obj.content = "对象中的内容"

            text = provider._extract_text(obj)
            assert text == "对象中的内容"

    def test_extract_text_from_dict(self, emotion_config):
        """测试从dict提取文本（应该返回None）"""
        with patch('src.providers.emotion_judge.emotion_judge_decision_provider.AsyncOpenAI'):
            provider = EmotionJudgeDecisionProvider(emotion_config)

            text = provider._extract_text({"key": "value"})
            assert text is None

    @pytest.mark.asyncio
    async def test_handle_canonical_message_in_cooldown(self, emotion_config, mock_event_bus):
        """测试冷却时间内的消息处理"""
        with patch('src.providers.emotion_judge.emotion_judge_decision_provider.AsyncOpenAI'):
            provider = EmotionJudgeDecisionProvider(emotion_config)
            await provider.setup(mock_event_bus)

            # 设置最近触发时间（刚刚触发）
            provider.last_trigger_time = time.monotonic()

            # 等待一小段时间确保仍在冷却中
            await asyncio.sleep(0.1)

            # 尝试处理消息
            await provider._handle_canonical_message("test_event", "测试消息", "test_source")

            # 由于在冷却期内，不应该触发情感判断
            # 这里我们只验证没有抛出异常即可

    @pytest.mark.asyncio
    async def test_judge_and_trigger_without_client(self, emotion_config, mock_event_bus):
        """测试没有客户端时的情感判断"""
        # 创建没有API key的配置
        config = {"cool_down_seconds": 5}
        provider = EmotionJudgeDecisionProvider(config)
        await provider.setup(mock_event_bus)

        # 应该不抛出异常
        await provider._judge_and_trigger("测试文本")

    @pytest.mark.asyncio
    async def test_cleanup(self, emotion_config):
        """测试清理资源"""
        with patch('src.providers.emotion_judge.emotion_judge_decision_provider.AsyncOpenAI') as mock_openai:
            mock_client = MagicMock()
            mock_client.close = AsyncMock()
            mock_openai.return_value = mock_client

            provider = EmotionJudgeDecisionProvider(emotion_config)

            await provider.cleanup()

            # 验证客户端被关闭
            if provider.client:
                mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_workflow_with_mock_llm(self, emotion_config, mock_event_bus):
        """测试完整的情感判断工作流（mock LLM）"""
        # Mock OpenAI客户端和响应
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "开心"

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        # Mock热键列表
        mock_hotkey_list = [
            {"name": "开心", "id": "happy_1"},
            {"name": "难过", "id": "sad_1"},
        ]

        with patch('src.providers.emotion_judge.emotion_judge_decision_provider.AsyncOpenAI', return_value=mock_client):
            provider = EmotionJudgeDecisionProvider(emotion_config)
            await provider.setup(mock_event_bus)

            # 确保不在冷却期
            provider.last_trigger_time = 0

            # Mock _get_hotkey_list 返回热键列表
            provider._get_hotkey_list = AsyncMock(return_value=mock_hotkey_list)

            # Mock _trigger_hotkey
            provider._trigger_hotkey = AsyncMock()

            # 执行情感判断
            await provider._judge_and_trigger("今天天气真好！")

            # 验证LLM API被调用
            mock_client.chat.completions.create.assert_called_once()

            # 验证触发热键
            provider._trigger_hotkey.assert_called_once_with("开心")
