"""
Intent 类型单元测试

测试跨域共享的 Intent 相关类型：
- EmotionType 枚举
- ActionType 枚举
- IntentAction 类
- Intent 类完整功能
- 序列化/反序列化

运行: uv run pytest tests/core/types/test_intent.py -v
"""

import pytest
import time
from typing import Dict, Any

from src.core.types import EmotionType, ActionType, IntentAction
from src.domains.decision.intent import Intent, SourceContext, ActionSuggestion


# =============================================================================
# EmotionType 测试
# =============================================================================


class TestEmotionType:
    """测试 EmotionType 枚举"""

    def test_emotion_type_values(self):
        """测试所有情感类型值"""
        assert EmotionType.NEUTRAL.value == "neutral"
        assert EmotionType.HAPPY.value == "happy"
        assert EmotionType.SAD.value == "sad"
        assert EmotionType.ANGRY.value == "angry"
        assert EmotionType.SURPRISED.value == "surprised"
        assert EmotionType.LOVE.value == "love"
        assert EmotionType.SHY.value == "shy"
        assert EmotionType.EXCITED.value == "excited"
        assert EmotionType.CONFUSED.value == "confused"
        assert EmotionType.SCARED.value == "scared"

    def test_emotion_type_get_default(self):
        """测试获取默认情感类型"""
        default = EmotionType.get_default()
        assert default == EmotionType.NEUTRAL

    def test_emotion_type_iteration(self):
        """测试遍历所有情感类型"""
        emotions = list(EmotionType)
        assert len(emotions) == 10
        assert EmotionType.HAPPY in emotions
        assert EmotionType.SAD in emotions

    def test_emotion_type_comparison(self):
        """测试情感类型比较"""
        assert EmotionType.HAPPY == EmotionType.HAPPY
        assert EmotionType.HAPPY != EmotionType.SAD

    def test_emotion_type_string_value(self):
        """测试获取字符串值"""
        # 枚举的 str() 返回 EmotionType.HAPPY 格式
        assert "HAPPY" in str(EmotionType.HAPPY)
        # 使用 .value 获取字符串值
        assert EmotionType.HAPPY.value == "happy"


# =============================================================================
# ActionType 测试
# =============================================================================


class TestActionType:
    """测试 ActionType 枚举"""

    def test_action_type_values(self):
        """测试所有动作类型值"""
        assert ActionType.EXPRESSION.value == "expression"
        assert ActionType.HOTKEY.value == "hotkey"
        assert ActionType.EMOJI.value == "emoji"
        assert ActionType.BLINK.value == "blink"
        assert ActionType.NOD.value == "nod"
        assert ActionType.SHAKE.value == "shake"
        assert ActionType.WAVE.value == "wave"
        assert ActionType.CLAP.value == "clap"
        assert ActionType.STICKER.value == "sticker"
        assert ActionType.MOTION.value == "motion"
        assert ActionType.CUSTOM.value == "custom"
        assert ActionType.GAME_ACTION.value == "game_action"
        assert ActionType.NONE.value == "none"

    def test_action_type_get_default(self):
        """测试获取默认动作类型"""
        default = ActionType.get_default()
        assert default == ActionType.EXPRESSION

    def test_action_type_iteration(self):
        """测试遍历所有动作类型"""
        actions = list(ActionType)
        # 当前有13个动作类型（不包括某个已移除的类型）
        assert len(actions) >= 13
        assert ActionType.BLINK in actions
        assert ActionType.WAVE in actions

    def test_action_type_comparison(self):
        """测试动作类型比较"""
        assert ActionType.BLINK == ActionType.BLINK
        assert ActionType.BLINK != ActionType.WAVE


# =============================================================================
# IntentAction 测试
# =============================================================================


class TestIntentAction:
    """测试 IntentAction 类"""

    def test_intent_action_creation(self):
        """测试创建 IntentAction"""
        action = IntentAction(
            type=ActionType.BLINK,
            params={"count": 2},
            priority=30,
        )
        assert action.type == ActionType.BLINK
        assert action.params == {"count": 2}
        assert action.priority == 30

    def test_intent_action_default_params(self):
        """测试默认 params 为空字典"""
        action = IntentAction(type=ActionType.WAVE)
        assert action.params == {}

    def test_intent_action_default_priority(self):
        """测试默认 priority 为 50"""
        action = IntentAction(type=ActionType.NOD)
        assert action.priority == 50

    def test_intent_action_priority_validation(self):
        """测试 priority 字段验证 (0-100)"""
        # 有效范围
        action = IntentAction(
            type=ActionType.BLINK,
            priority=0,
        )
        assert action.priority == 0

        action = IntentAction(
            type=ActionType.BLINK,
            priority=100,
        )
        assert action.priority == 100

    def test_intent_action_priority_invalid(self):
        """测试无效的 priority 值"""
        # 超出范围
        with pytest.raises(Exception):  # ValidationError
            IntentAction(
                type=ActionType.BLINK,
                priority=150,
            )

        # 负数
        with pytest.raises(Exception):  # ValidationError
            IntentAction(
                type=ActionType.BLINK,
                priority=-10,
            )

    def test_intent_action_serialization(self):
        """测试序列化"""
        action = IntentAction(
            type=ActionType.BLINK,
            params={"count": 2},
            priority=30,
        )
        data = action.model_dump()
        assert data["type"] == "blink"  # use_enum_values=True
        assert data["params"] == {"count": 2}
        assert data["priority"] == 30

    def test_intent_action_deserialization(self):
        """测试反序列化"""
        data = {
            "type": "blink",
            "params": {"count": 2},
            "priority": 30,
        }
        action = IntentAction.model_validate(data)
        assert action.type == ActionType.BLINK
        assert action.params == {"count": 2}
        assert action.priority == 30


# =============================================================================
# SourceContext 测试
# =============================================================================


class TestSourceContext:
    """测试 SourceContext 类"""

    def test_source_context_creation(self):
        """测试创建 SourceContext"""
        context = SourceContext(
            source="console_input",
            data_type="text",
            user_id="12345",
            user_nickname="测试用户",
            importance=0.8,
        )
        assert context.source == "console_input"
        assert context.data_type == "text"
        assert context.user_id == "12345"
        assert context.user_nickname == "测试用户"
        assert context.importance == 0.8

    def test_source_context_optional_fields(self):
        """测试可选字段"""
        context = SourceContext(
            source="bili_danmaku",
            data_type="gift",
        )
        assert context.source == "bili_danmaku"
        assert context.data_type == "gift"
        assert context.user_id is None
        assert context.user_nickname is None

    def test_source_context_default_importance(self):
        """测试默认 importance 为 0.5"""
        context = SourceContext(
            source="test",
            data_type="test",
        )
        assert context.importance == 0.5

    def test_source_context_importance_validation(self):
        """测试 importance 字段验证 (0.0-1.0)"""
        # 有效范围
        context = SourceContext(
            source="test",
            data_type="test",
            importance=0.0,
        )
        assert context.importance == 0.0

        context = SourceContext(
            source="test",
            data_type="test",
            importance=1.0,
        )
        assert context.importance == 1.0

    def test_source_context_importance_invalid(self):
        """测试无效的 importance 值"""
        with pytest.raises(Exception):  # ValidationError
            SourceContext(
                source="test",
                data_type="test",
                importance=1.5,
            )

        with pytest.raises(Exception):  # ValidationError
            SourceContext(
                source="test",
                data_type="test",
                importance=-0.1,
            )

    def test_source_context_extra_metadata(self):
        """测试额外元数据"""
        context = SourceContext(
            source="test",
            data_type="test",
            extra={"room_id": "123456", "gift_name": "鲜花"},
        )
        assert context.extra["room_id"] == "123456"
        assert context.extra["gift_name"] == "鲜花"


# =============================================================================
# ActionSuggestion 测试
# =============================================================================


class TestActionSuggestion:
    """测试 ActionSuggestion 类"""

    def test_action_suggestion_creation(self):
        """测试创建 ActionSuggestion"""
        suggestion = ActionSuggestion(
            action_name="wave",
            priority=50,
            parameters={"duration": 1.0},
            reason="用户打招呼",
            confidence=0.9,
        )
        assert suggestion.action_name == "wave"
        assert suggestion.priority == 50
        assert suggestion.parameters == {"duration": 1.0}
        assert suggestion.reason == "用户打招呼"
        assert suggestion.confidence == 0.9

    def test_action_suggestion_defaults(self):
        """测试默认字段值"""
        suggestion = ActionSuggestion(action_name="smile")
        assert suggestion.priority == 50
        assert suggestion.parameters == {}
        assert suggestion.reason == ""
        assert suggestion.confidence == 0.5

    def test_action_suggestion_priority_validation(self):
        """测试 priority 字段验证 (0-100)"""
        suggestion = ActionSuggestion(
            action_name="test",
            priority=75,
        )
        assert suggestion.priority == 75

    def test_action_suggestion_confidence_validation(self):
        """测试 confidence 字段验证 (0.0-1.0)"""
        suggestion = ActionSuggestion(
            action_name="test",
            confidence=0.8,
        )
        assert suggestion.confidence == 0.8


# =============================================================================
# Intent 测试
# =============================================================================


class TestIntent:
    """测试 Intent 类"""

    def test_intent_creation_minimal(self):
        """测试创建最小 Intent"""
        intent = Intent(
            original_text="你好",
            response_text="你好！很高兴见到你~",
        )
        assert intent.original_text == "你好"
        assert intent.response_text == "你好！很高兴见到你~"
        assert intent.emotion == EmotionType.NEUTRAL
        assert intent.actions == []
        assert intent.source_context is None

    def test_intent_creation_full(self):
        """测试创建完整 Intent"""
        source_context = SourceContext(
            source="console_input",
            data_type="text",
            user_id="123",
        )
        actions = [
            IntentAction(type=ActionType.BLINK, params={"count": 2}),
            IntentAction(type=ActionType.WAVE, params={"duration": 1.0}),
        ]

        intent = Intent(
            original_text="测试",
            response_text="回复",
            emotion=EmotionType.HAPPY,
            actions=actions,
            source_context=source_context,
            metadata={"confidence": 0.95},
        )
        assert intent.original_text == "测试"
        assert intent.response_text == "回复"
        assert intent.emotion == EmotionType.HAPPY
        assert len(intent.actions) == 2
        assert intent.source_context.source == "console_input"
        assert intent.metadata["confidence"] == 0.95

    def test_intent_id_generation(self):
        """测试自动生成唯一 ID"""
        intent1 = Intent(
            original_text="测试1",
            response_text="回复1",
        )
        intent2 = Intent(
            original_text="测试2",
            response_text="回复2",
        )
        assert intent1.id != intent2.id
        assert len(intent1.id) > 0

    def test_intent_timestamp_generation(self):
        """测试自动生成时间戳"""
        before = time.time()
        intent = Intent(
            original_text="测试",
            response_text="回复",
        )
        after = time.time()
        assert before <= intent.timestamp <= after

    def test_intent_suggested_actions(self):
        """测试建议的动作列表"""
        suggestions = [
            ActionSuggestion(action_name="smile", reason="表示友好"),
            ActionSuggestion(action_name="wave", reason="打招呼"),
        ]
        intent = Intent(
            original_text="你好",
            response_text="你好呀~",
            suggested_actions=suggestions,
        )
        assert intent.suggested_actions is not None
        assert len(intent.suggested_actions) == 2
        assert intent.suggested_actions[0].action_name == "smile"

    def test_intent_actions_mutation(self):
        """测试 actions 列表可变性"""
        intent = Intent(
            original_text="测试",
            response_text="回复",
        )
        assert len(intent.actions) == 0

        # 添加动作
        intent.actions.append(IntentAction(type=ActionType.BLINK))
        assert len(intent.actions) == 1

    def test_intent_repr(self):
        """测试 __repr__ 方法"""
        intent = Intent(
            original_text="这是一个很长的测试文本，用于测试字符串截断功能是否正常工作",
            response_text="这是回复文本",
            emotion=EmotionType.HAPPY,
            actions=[
                IntentAction(type=ActionType.BLINK),
                IntentAction(type=ActionType.WAVE),
            ],
        )
        repr_str = repr(intent)
        assert "Intent(" in repr_str
        # emotion 的 repr 包含枚举类型名
        assert "EmotionType.HAPPY" in repr_str or "emotion=" in repr_str
        assert "actions=2" in repr_str
        assert "response_text=" in repr_str

    def test_intent_serialization(self):
        """测试序列化"""
        intent = Intent(
            original_text="测试",
            response_text="回复",
            emotion=EmotionType.EXCITED,
            actions=[
                IntentAction(type=ActionType.BLINK, params={"count": 1}),
            ],
        )
        data = intent.model_dump()
        assert data["original_text"] == "测试"
        assert data["response_text"] == "回复"
        assert data["emotion"] == "excited"  # use_enum_values
        assert len(data["actions"]) == 1
        assert data["actions"][0]["type"] == "blink"

    def test_intent_deserialization(self):
        """测试反序列化"""
        data = {
            "original_text": "测试",
            "response_text": "回复",
            "emotion": "happy",
            "actions": [
                {"type": "blink", "params": {"count": 2}, "priority": 30},
            ],
            "metadata": {"test": "value"},
            "timestamp": time.time(),
        }
        intent = Intent.model_validate(data)
        assert intent.original_text == "测试"
        assert intent.response_text == "回复"
        assert intent.emotion == EmotionType.HAPPY
        assert len(intent.actions) == 1
        assert intent.actions[0].type == ActionType.BLINK

    def test_intent_json_schema(self):
        """测试 JSON Schema 生成"""
        schema = Intent.model_json_schema()
        assert "properties" in schema
        assert "original_text" in schema["properties"]
        assert "response_text" in schema["properties"]
        assert "emotion" in schema["properties"]
        assert "actions" in schema["properties"]


# =============================================================================
# 集成测试
# =============================================================================


class TestIntentIntegration:
    """Intent 集成测试"""

    def test_intent_with_complex_actions(self):
        """测试包含复杂动作的 Intent"""
        intent = Intent(
            original_text="用户发了一个红包",
            response_text="谢谢老板！",
            emotion=EmotionType.EXCITED,
            actions=[
                IntentAction(
                    type=ActionType.EXPRESSION,
                    params={"expression": "surprised"},
                    priority=80,
                ),
                IntentAction(
                    type=ActionType.BLINK,
                    params={"count": 3},
                    priority=70,
                ),
                IntentAction(
                    type=ActionType.WAVE,
                    params={"duration": 2.0},
                    priority=60,
                ),
            ],
        )
        assert len(intent.actions) == 3
        # 验证动作按优先级排序（如果需要）
        priorities = [a.priority for a in intent.actions]
        assert priorities == [80, 70, 60]

    def test_intent_round_trip(self):
        """测试序列化-反序列化往返"""
        original = Intent(
            original_text="原始消息",
            response_text="回复消息",
            emotion=EmotionType.LOVE,
            actions=[
                IntentAction(type=ActionType.CLAP, params={"count": 5}),
            ],
            source_context=SourceContext(
                source="bili_danmaku",
                data_type="gift",
                user_id="999",
                user_nickname="慷慨的观众",
                importance=1.0,
            ),
            metadata={"gift_value": 100},
        )

        # 序列化
        data = original.model_dump()

        # 反序列化
        restored = Intent.model_validate(data)

        # 验证
        assert restored.original_text == original.original_text
        assert restored.response_text == original.response_text
        assert restored.emotion == original.emotion
        assert len(restored.actions) == len(original.actions)
        assert restored.actions[0].type == ActionType.CLAP
        assert restored.source_context.source == "bili_danmaku"
        assert restored.source_context.importance == 1.0
        assert restored.metadata["gift_value"] == 100


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
