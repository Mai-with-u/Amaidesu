"""
测试 Intent 数据结构 (pytest)

运行: uv run pytest tests/layers/decision/test_intent.py -v
"""

import pytest
import time
from src.layers.decision.intent import (
    Intent,
    IntentAction,
    EmotionType,
    ActionType,
)


# =============================================================================
# EmotionType 枚举测试
# =============================================================================

class TestEmotionType:
    """测试 EmotionType 枚举"""

    def test_emotion_type_values(self):
        """测试所有情感类型的值"""
        assert EmotionType.NEUTRAL.value == "neutral"
        assert EmotionType.HAPPY.value == "happy"
        assert EmotionType.SAD.value == "sad"
        assert EmotionType.ANGRY.value == "angry"
        assert EmotionType.SURPRISED.value == "surprised"
        assert EmotionType.LOVE.value == "love"

    def test_emotion_type_from_string(self):
        """测试从字符串创建 EmotionType"""
        assert EmotionType("neutral") == EmotionType.NEUTRAL
        assert EmotionType("happy") == EmotionType.HAPPY
        assert EmotionType("sad") == EmotionType.SAD
        assert EmotionType("angry") == EmotionType.ANGRY
        assert EmotionType("surprised") == EmotionType.SURPRISED
        assert EmotionType("love") == EmotionType.LOVE

    def test_emotion_type_invalid_string(self):
        """测试无效字符串抛出 ValueError"""
        with pytest.raises(ValueError, match="is not a valid EmotionType"):
            EmotionType("invalid_emotion")


# =============================================================================
# ActionType 枚举测试
# =============================================================================

class TestActionType:
    """测试 ActionType 枚举"""

    def test_action_type_values(self):
        """测试所有动作类型的值"""
        assert ActionType.EXPRESSION.value == "expression"
        assert ActionType.HOTKEY.value == "hotkey"
        assert ActionType.EMOJI.value == "emoji"
        assert ActionType.BLINK.value == "blink"
        assert ActionType.NOD.value == "nod"
        assert ActionType.SHAKE.value == "shake"
        assert ActionType.WAVE.value == "wave"
        assert ActionType.CLAP.value == "clap"
        assert ActionType.NONE.value == "none"

    def test_action_type_from_string(self):
        """测试从字符串创建 ActionType"""
        assert ActionType("expression") == ActionType.EXPRESSION
        assert ActionType("hotkey") == ActionType.HOTKEY
        assert ActionType("emoji") == ActionType.EMOJI
        assert ActionType("blink") == ActionType.BLINK
        assert ActionType("nod") == ActionType.NOD
        assert ActionType("shake") == ActionType.SHAKE
        assert ActionType("wave") == ActionType.WAVE
        assert ActionType("clap") == ActionType.CLAP
        assert ActionType("none") == ActionType.NONE

    def test_action_type_invalid_string(self):
        """测试无效字符串抛出 ValueError"""
        with pytest.raises(ValueError, match="is not a valid ActionType"):
            ActionType("invalid_action")


# =============================================================================
# IntentAction 测试
# =============================================================================

class TestIntentAction:
    """测试 IntentAction 数据类"""

    def test_create_intent_action_with_all_params(self):
        """测试创建完整的 IntentAction"""
        action = IntentAction(
            type=ActionType.EXPRESSION,
            params={"name": "smile"},
            priority=80
        )

        assert action.type == ActionType.EXPRESSION
        assert action.params == {"name": "smile"}
        assert action.priority == 80

    def test_create_intent_action_with_default_priority(self):
        """测试使用默认优先级创建 IntentAction"""
        action = IntentAction(
            type=ActionType.BLINK,
            params={}
        )

        assert action.type == ActionType.BLINK
        assert action.params == {}
        assert action.priority == 50  # 默认值

    def test_intent_action_repr(self):
        """测试 IntentAction 的字符串表示"""
        action = IntentAction(
            type=ActionType.WAVE,
            params={"intensity": 0.8},
            priority=70
        )

        repr_str = repr(action)
        assert "wave" in repr_str
        assert "70" in repr_str
        assert "params" in repr_str

    def test_intent_action_different_types(self):
        """测试不同类型的 IntentAction"""
        actions = [
            IntentAction(type=ActionType.EXPRESSION, params={"name": "sad"}),
            IntentAction(type=ActionType.HOTKEY, params={"key": "F1"}),
            IntentAction(type=ActionType.EMOJI, params={"emoji": "😀"}),
            IntentAction(type=ActionType.BLINK, params={}),
            IntentAction(type=ActionType.NOD, params={}),
            IntentAction(type=ActionType.SHAKE, params={}),
            IntentAction(type=ActionType.WAVE, params={}),
            IntentAction(type=ActionType.CLAP, params={}),
            IntentAction(type=ActionType.NONE, params={}),
        ]

        assert len(actions) == 9
        assert actions[0].type == ActionType.EXPRESSION
        assert actions[1].type == ActionType.HOTKEY
        assert actions[2].type == ActionType.EMOJI
        assert actions[3].type == ActionType.BLINK
        assert actions[4].type == ActionType.NOD
        assert actions[5].type == ActionType.SHAKE
        assert actions[6].type == ActionType.WAVE
        assert actions[7].type == ActionType.CLAP
        assert actions[8].type == ActionType.NONE


# =============================================================================
# Intent 测试
# =============================================================================

class TestIntentCreation:
    """测试 Intent 创建和初始化"""

    def test_create_intent_with_all_fields(self):
        """测试创建完整的 Intent"""
        actions = [
            IntentAction(type=ActionType.BLINK, params={}, priority=30),
            IntentAction(type=ActionType.EXPRESSION, params={"name": "smile"}, priority=60)
        ]

        intent = Intent(
            original_text="你好呀",
            response_text="你好！很高兴见到你！",
            emotion=EmotionType.HAPPY,
            actions=actions,
            metadata={"source": "test", "confidence": 0.95},
            timestamp=1234567890.0
        )

        assert intent.original_text == "你好呀"
        assert intent.response_text == "你好！很高兴见到你！"
        assert intent.emotion == EmotionType.HAPPY
        assert len(intent.actions) == 2
        assert intent.metadata == {"source": "test", "confidence": 0.95}
        assert intent.timestamp == 1234567890.0

    def test_create_intent_with_default_timestamp(self):
        """测试使用默认时间戳创建 Intent"""
        before = time.time()
        intent = Intent(
            original_text="test",
            response_text="response",
            emotion=EmotionType.NEUTRAL,
            actions=[],
            metadata={}
        )
        after = time.time()

        assert before <= intent.timestamp <= after

    def test_create_intent_with_none_metadata(self):
        """测试 metadata 为 None 时转换为空字典"""
        intent = Intent(
            original_text="test",
            response_text="response",
            emotion=EmotionType.NEUTRAL,
            actions=[],
            metadata=None
        )

        assert intent.metadata == {}

    def test_create_intent_metadata_isolation(self):
        """测试 metadata 的隔离（修改不影响原始字典）"""
        original_metadata = {"key": "value"}
        intent = Intent(
            original_text="test",
            response_text="response",
            emotion=EmotionType.NEUTRAL,
            actions=[],
            metadata=original_metadata
        )

        # 修改 intent 的 metadata
        intent.metadata["new_key"] = "new_value"

        # 原始字典不应被修改
        assert "new_key" not in original_metadata

    def test_create_intent_empty_actions(self):
        """测试空动作列表"""
        intent = Intent(
            original_text="test",
            response_text="response",
            emotion=EmotionType.NEUTRAL,
            actions=[],
            metadata={}
        )

        assert intent.actions == []

    def test_create_intent_with_different_emotions(self):
        """测试不同情感类型"""
        emotions = [
            EmotionType.NEUTRAL,
            EmotionType.HAPPY,
            EmotionType.SAD,
            EmotionType.ANGRY,
            EmotionType.SURPRISED,
            EmotionType.LOVE,
        ]

        for emotion in emotions:
            intent = Intent(
                original_text="test",
                response_text="response",
                emotion=emotion,
                actions=[],
                metadata={}
            )
            assert intent.emotion == emotion


class TestIntentSerialization:
    """测试 Intent 序列化和反序列化"""

    def test_to_dict_simple(self):
        """测试简单的 Intent 转字典"""
        intent = Intent(
            original_text="hello",
            response_text="hi there",
            emotion=EmotionType.HAPPY,
            actions=[
                IntentAction(type=ActionType.BLINK, params={}, priority=30)
            ],
            metadata={"source": "test"}
        )

        result = intent.to_dict()

        assert result["original_text"] == "hello"
        assert result["response_text"] == "hi there"
        assert result["emotion"] == "happy"
        assert len(result["actions"]) == 1
        assert result["actions"][0]["type"] == "blink"
        assert result["actions"][0]["params"] == {}
        assert result["actions"][0]["priority"] == 30
        assert result["metadata"] == {"source": "test"}
        assert "timestamp" in result

    def test_to_dict_complex_actions(self):
        """测试包含复杂动作的 Intent 转字典"""
        intent = Intent(
            original_text="test",
            response_text="response",
            emotion=EmotionType.NEUTRAL,
            actions=[
                IntentAction(type=ActionType.EXPRESSION, params={"name": "smile"}, priority=70),
                IntentAction(type=ActionType.EMOJI, params={"emoji": "😀"}, priority=80),
                IntentAction(type=ActionType.WAVE, params={"intensity": 0.9}, priority=60),
            ],
            metadata={}
        )

        result = intent.to_dict()

        assert len(result["actions"]) == 3
        assert result["actions"][0]["type"] == "expression"
        assert result["actions"][0]["params"]["name"] == "smile"
        assert result["actions"][1]["type"] == "emoji"
        assert result["actions"][1]["params"]["emoji"] == "😀"
        assert result["actions"][2]["type"] == "wave"
        assert result["actions"][2]["params"]["intensity"] == 0.9

    def test_to_dict_metadata_copy(self):
        """测试 to_dict 时 metadata 被复制"""
        intent = Intent(
            original_text="test",
            response_text="response",
            emotion=EmotionType.NEUTRAL,
            actions=[],
            metadata={"key": "value"}
        )

        result = intent.to_dict()

        # 修改返回的字典
        result["metadata"]["new_key"] = "new_value"

        # 原始 intent 的 metadata 不应被修改
        assert "new_key" not in intent.metadata

    def test_from_dict_simple(self):
        """测试从简单字典创建 Intent"""
        data = {
            "original_text": "hello",
            "response_text": "hi there",
            "emotion": "happy",
            "actions": [
                {"type": "blink", "params": {}, "priority": 30}
            ],
            "metadata": {"source": "test"},
            "timestamp": 1234567890.0
        }

        intent = Intent.from_dict(data)

        assert intent.original_text == "hello"
        assert intent.response_text == "hi there"
        assert intent.emotion == EmotionType.HAPPY
        assert len(intent.actions) == 1
        assert intent.actions[0].type == ActionType.BLINK
        assert intent.actions[0].params == {}
        assert intent.actions[0].priority == 30
        assert intent.metadata == {"source": "test"}
        assert intent.timestamp == 1234567890.0

    def test_from_dict_with_default_values(self):
        """测试从字典创建 Intent 时使用默认值"""
        data = {
            "original_text": "test",
            "response_text": "response",
        }

        intent = Intent.from_dict(data)

        assert intent.original_text == "test"
        assert intent.response_text == "response"
        assert intent.emotion == EmotionType.NEUTRAL
        assert intent.actions == []
        assert intent.metadata == {}
        assert isinstance(intent.timestamp, float)

    def test_from_dict_missing_actions(self):
        """测试从字典创建 Intent 时缺少 actions"""
        data = {
            "original_text": "test",
            "response_text": "response",
            "emotion": "sad"
        }

        intent = Intent.from_dict(data)

        assert intent.actions == []

    def test_from_dict_missing_priority(self):
        """测试从字典创建 Intent 时动作缺少 priority"""
        data = {
            "original_text": "test",
            "response_text": "response",
            "emotion": "neutral",
            "actions": [
                {"type": "blink", "params": {}}
            ]
        }

        intent = Intent.from_dict(data)

        assert intent.actions[0].priority == 50  # 默认值

    def test_from_dict_invalid_emotion(self):
        """测试无效的情感类型"""
        data = {
            "original_text": "test",
            "response_text": "response",
            "emotion": "invalid_emotion",
            "actions": []
        }

        with pytest.raises(ValueError, match="is not a valid EmotionType"):
            Intent.from_dict(data)

    def test_from_dict_invalid_action_type(self):
        """测试无效的动作类型"""
        data = {
            "original_text": "test",
            "response_text": "response",
            "emotion": "neutral",
            "actions": [
                {"type": "invalid_action", "params": {}}
            ]
        }

        with pytest.raises(ValueError, match="is not a valid ActionType"):
            Intent.from_dict(data)

    def test_round_trip_serialization(self):
        """测试序列化后再反序列化（往返测试）"""
        original = Intent(
            original_text="你好",
            response_text="你好啊！",
            emotion=EmotionType.LOVE,
            actions=[
                IntentAction(type=ActionType.EXPRESSION, params={"name": "blush"}, priority=80),
                IntentAction(type=ActionType.WAVE, params={}, priority=60)
            ],
            metadata={"confidence": 0.9, "model": "test-model"},
            timestamp=1234567890.0
        )

        # 序列化
        data = original.to_dict()

        # 反序列化
        restored = Intent.from_dict(data)

        # 验证所有字段
        assert restored.original_text == original.original_text
        assert restored.response_text == original.response_text
        assert restored.emotion == original.emotion
        assert len(restored.actions) == len(original.actions)
        assert restored.actions[0].type == original.actions[0].type
        assert restored.actions[0].params == original.actions[0].params
        assert restored.actions[0].priority == original.actions[0].priority
        assert restored.actions[1].type == original.actions[1].type
        assert restored.metadata == original.metadata
        assert restored.timestamp == original.timestamp


class TestIntentRepresentation:
    """测试 Intent 的字符串表示"""

    def test_intent_repr(self):
        """测试 Intent 的 __repr__ 方法"""
        intent = Intent(
            original_text="这是一个很长的测试文本，用来测试 repr 的截断功能是否正常工作",
            response_text="这是一个很长的回复文本，也应该被截断显示",
            emotion=EmotionType.HAPPY,
            actions=[
                IntentAction(type=ActionType.BLINK, params={}, priority=30),
                IntentAction(type=ActionType.EXPRESSION, params={"name": "smile"}, priority=60)
            ],
            metadata={}
        )

        repr_str = repr(intent)

        assert "happy" in repr_str
        assert "2" in repr_str  # actions 数量
        assert "..." in repr_str  # 截断标记

    def test_intent_repr_short_text(self):
        """测试短文本的 repr"""
        intent = Intent(
            original_text="short",
            response_text="reply",
            emotion=EmotionType.SAD,
            actions=[],
            metadata={}
        )

        repr_str = repr(intent)

        assert "sad" in repr_str
        assert "0" in repr_str  # actions 数量


class TestIntentEdgeCases:
    """测试 Intent 边界情况"""

    def test_empty_text(self):
        """测试空文本"""
        intent = Intent(
            original_text="",
            response_text="",
            emotion=EmotionType.NEUTRAL,
            actions=[],
            metadata={}
        )

        assert intent.original_text == ""
        assert intent.response_text == ""

    def test_very_long_text(self):
        """测试超长文本"""
        long_text = "测试" * 10000
        intent = Intent(
            original_text=long_text,
            response_text=long_text,
            emotion=EmotionType.NEUTRAL,
            actions=[],
            metadata={}
        )

        assert intent.original_text == long_text
        assert intent.response_text == long_text

    def test_special_characters_in_text(self):
        """测试文本中的特殊字符"""
        special_text = "测试\n换行\t制表符\r回车\"引号\'单引号\\反斜杠"
        intent = Intent(
            original_text=special_text,
            response_text=special_text,
            emotion=EmotionType.NEUTRAL,
            actions=[],
            metadata={}
        )

        assert intent.original_text == special_text
        assert intent.response_text == special_text

    def test_unicode_emoji(self):
        """测试 Unicode emoji"""
        intent = Intent(
            original_text="😀😃😄😁😆😅🤣😂",
            response_text="❤️💕💖💗💓💝",
            emotion=EmotionType.LOVE,
            actions=[
                IntentAction(type=ActionType.EMOJI, params={"emoji": "😀"}, priority=50)
            ],
            metadata={}
        )

        assert "😀" in intent.original_text
        assert "❤️" in intent.response_text

    def test_empty_params_in_action(self):
        """测试动作中空 params"""
        action = IntentAction(
            type=ActionType.BLINK,
            params={},
            priority=30
        )

        assert action.params == {}

    def test_complex_nested_params(self):
        """测试复杂的嵌套 params"""
        complex_params = {
            "nested": {
                "deep": {
                    "value": 123,
                    "list": [1, 2, 3]
                }
            },
            "array": [{"a": 1}, {"b": 2}]
        }

        action = IntentAction(
            type=ActionType.EXPRESSION,
            params=complex_params,
            priority=70
        )

        assert action.params == complex_params

    def test_many_actions(self):
        """测试大量动作"""
        actions = [
            IntentAction(
                type=ActionType.BLINK,
                params={"index": i},
                priority=i
            )
            for i in range(100)
        ]

        intent = Intent(
            original_text="test",
            response_text="response",
            emotion=EmotionType.NEUTRAL,
            actions=actions,
            metadata={}
        )

        assert len(intent.actions) == 100
        assert intent.actions[0].priority == 0
        assert intent.actions[99].priority == 99

    def test_metadata_with_various_types(self):
        """测试 metadata 包含各种数据类型"""
        metadata = {
            "string": "value",
            "int": 42,
            "float": 3.14,
            "bool": True,
            "null": None,
            "list": [1, 2, 3],
            "dict": {"nested": "value"},
            "tuple": (1, 2, 3),  # 会被转字典
        }

        intent = Intent(
            original_text="test",
            response_text="response",
            emotion=EmotionType.NEUTRAL,
            actions=[],
            metadata=metadata
        )

        assert intent.metadata == metadata


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
