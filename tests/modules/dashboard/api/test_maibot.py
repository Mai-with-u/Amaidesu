"""
MaiBot API 测试
"""

from src.modules.dashboard.schemas.maibot import MaibotActionRequest, MaibotActionResponse
from src.modules.types.intent import ActionType, EmotionType


class TestMaibotActionRequest:
    """测试 MaibotActionRequest 模型"""

    def test_action_only(self):
        """测试仅包含动作的请求"""
        req = MaibotActionRequest(action=ActionType.HOTKEY, action_params={"key": "smile"})
        assert req.action == ActionType.HOTKEY
        assert req.action_params == {"key": "smile"}
        assert req.emotion is None
        assert req.priority == 50

    def test_emotion_only(self):
        """测试仅包含情绪的请求"""
        req = MaibotActionRequest(emotion=EmotionType.HAPPY)
        assert req.emotion == EmotionType.HAPPY
        assert req.action is None

    def test_action_and_emotion(self):
        """测试同时包含动作和情绪的请求"""
        req = MaibotActionRequest(
            action=ActionType.WAVE,
            emotion=EmotionType.EXCITED,
            text="大家好！",
            priority=80,
        )
        assert req.action == ActionType.WAVE
        assert req.emotion == EmotionType.EXCITED
        assert req.text == "大家好！"
        assert req.priority == 80

    def test_default_priority(self):
        """测试默认优先级"""
        req = MaibotActionRequest(emotion=EmotionType.NEUTRAL)
        assert req.priority == 50

    def test_priority_range(self):
        """测试优先级范围"""
        req = MaibotActionRequest(emotion=EmotionType.HAPPY, priority=0)
        assert req.priority == 0

        req = MaibotActionRequest(emotion=EmotionType.HAPPY, priority=100)
        assert req.priority == 100


class TestMaibotActionResponse:
    """测试 MaibotActionResponse 模型"""

    def test_success_response(self):
        """测试成功响应"""
        resp = MaibotActionResponse(success=True, intent_id="test-123", message="OK")
        assert resp.success is True
        assert resp.intent_id == "test-123"
        assert resp.message == "OK"
        assert resp.error is None

    def test_error_response(self):
        """测试错误响应"""
        resp = MaibotActionResponse(success=False, error="Event bus not available")
        assert resp.success is False
        assert resp.error == "Event bus not available"
