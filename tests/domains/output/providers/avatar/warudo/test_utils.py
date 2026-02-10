"""Warudo 工具模块测试"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domains.output.providers.avatar.warudo.utils import IntentEmotionAdapter
from src.domains.output.providers.avatar.warudo.warudo_sender import ActionSender
from src.modules.types.intent import EmotionType


class TestIntentEmotionAdapter:
    """IntentEmotionAdapter 测试"""

    def test_adapt_neutral_emotion(self):
        """测试适配中性情绪"""
        result = IntentEmotionAdapter.adapt(EmotionType.NEUTRAL)
        assert result == {"joy": 5, "anger": 1, "sorrow": 1, "fear": 1}

    def test_adapt_happy_emotion(self):
        """测试适配快乐情绪"""
        result = IntentEmotionAdapter.adapt(EmotionType.HAPPY)
        assert result == {"joy": 7, "anger": 1, "sorrow": 1, "fear": 1}

    def test_adapt_sad_emotion(self):
        """测试适配悲伤情绪"""
        result = IntentEmotionAdapter.adapt(EmotionType.SAD)
        assert result == {"joy": 1, "anger": 1, "sorrow": 7, "fear": 1}

    def test_adapt_angry_emotion(self):
        """测试适配愤怒情绪"""
        result = IntentEmotionAdapter.adapt(EmotionType.ANGRY)
        assert result == {"joy": 1, "anger": 7, "sorrow": 1, "fear": 1}

    def test_adapt_surprised_emotion(self):
        """测试适配惊讶情绪"""
        result = IntentEmotionAdapter.adapt(EmotionType.SURPRISED)
        assert result == {"joy": 6, "anger": 1, "sorrow": 1, "fear": 6}

    def test_adapt_love_emotion(self):
        """测试适配爱意情绪"""
        result = IntentEmotionAdapter.adapt(EmotionType.LOVE)
        assert result == {"joy": 8, "anger": 1, "sorrow": 1, "fear": 1}

    def test_adapt_shy_emotion(self):
        """测试适配害羞情绪"""
        result = IntentEmotionAdapter.adapt(EmotionType.SHY)
        assert result == {"joy": 5, "anger": 1, "sorrow": 2, "fear": 4}

    def test_adapt_excited_emotion(self):
        """测试适配兴奋情绪"""
        result = IntentEmotionAdapter.adapt(EmotionType.EXCITED)
        assert result == {"joy": 9, "anger": 1, "sorrow": 1, "fear": 1}

    def test_adapt_confused_emotion(self):
        """测试适配困惑情绪"""
        result = IntentEmotionAdapter.adapt(EmotionType.CONFUSED)
        assert result == {"joy": 3, "anger": 2, "sorrow": 2, "fear": 5}

    def test_adapt_scared_emotion(self):
        """测试适配恐惧情绪"""
        result = IntentEmotionAdapter.adapt(EmotionType.SCARED)
        assert result == {"joy": 1, "anger": 1, "sorrow": 2, "fear": 9}

    def test_adapt_returns_copy(self):
        """测试返回的是副本而非原字典的引用"""
        result1 = IntentEmotionAdapter.adapt(EmotionType.HAPPY)
        result2 = IntentEmotionAdapter.adapt(EmotionType.HAPPY)

        # 修改 result1 不应影响 result2
        result1["joy"] = 10
        assert result2["joy"] == 7

    def test_all_emotions_in_range(self):
        """测试所有情绪值都在 1-10 范围内"""
        for emotion in EmotionType:
            result = IntentEmotionAdapter.adapt(emotion)
            for key, value in result.items():
                assert 1 <= value <= 10, f"{emotion}.{key} = {value} 不在 1-10 范围内"


class TestActionSender:
    """ActionSender 测试"""

    @pytest.fixture
    def mock_websocket(self):
        """创建 mock WebSocket 连接"""
        ws = MagicMock()
        ws.send = AsyncMock()
        return ws

    def test_init_without_websocket(self):
        """测试无 WebSocket 初始化"""
        sender = ActionSender()
        assert sender.websocket is None

    def test_init_with_websocket(self, mock_websocket):
        """测试带 WebSocket 初始化"""
        sender = ActionSender(mock_websocket)
        assert sender.websocket == mock_websocket

    def test_set_websocket(self, mock_websocket):
        """测试设置 WebSocket"""
        sender = ActionSender()
        sender.set_websocket(mock_websocket)
        assert sender.websocket == mock_websocket

    @pytest.mark.asyncio
    async def test_send_action_with_valid_websocket(self, mock_websocket):
        """测试使用有效 WebSocket 发送动作"""
        sender = ActionSender(mock_websocket)
        await sender.send_action("test_action", {"key": "value"})

        # 验证 WebSocket.send 被调用
        mock_websocket.send.assert_called_once()
        # 验证发送的消息是 JSON 格式
        import json

        sent_data = json.loads(mock_websocket.send.call_args[0][0])
        assert sent_data["action"] == "test_action"
        assert sent_data["data"] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_send_action_without_websocket(self):
        """测试无 WebSocket 时不发送动作"""
        sender = ActionSender()
        # 应该不抛出异常，只是静默失败
        await sender.send_action("test_action", {"key": "value"})

    @pytest.mark.asyncio
    async def test_send_action_with_preformatted_data(self, mock_websocket):
        """测试发送预格式化的数据（已包含 action 和 data 字段）"""
        sender = ActionSender(mock_websocket)
        preformatted = {"action": "custom_action", "data": {"custom": "data"}}
        await sender.send_action("ignored", preformatted)

        import json

        sent_data = json.loads(mock_websocket.send.call_args[0][0])
        assert sent_data["action"] == "custom_action"
        assert sent_data["data"] == {"custom": "data"}

    @pytest.mark.asyncio
    async def test_send_action_handles_exception(self, mock_websocket):
        """测试发送动作时的异常处理"""
        sender = ActionSender(mock_websocket)
        # 模拟 WebSocket 发送失败
        mock_websocket.send.side_effect = Exception("Connection lost")

        # 应该不抛出异常，只是打印错误
        await sender.send_action("test_action", {"key": "value"})
        # 异常被捕获，不会传播
        mock_websocket.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_action_multiple_calls(self, mock_websocket):
        """测试多次发送动作"""
        sender = ActionSender(mock_websocket)

        await sender.send_action("action1", {"data": "1"})
        await sender.send_action("action2", {"data": "2"})
        await sender.send_action("action3", {"data": "3"})

        assert mock_websocket.send.call_count == 3
