"""
Intent 类型单元测试

测试跨域共享的 Intent 相关类型：
- EmotionType 枚举
- ActionType 枚举
- IntentAction 类
- SourceContext 类
- Intent 类完整功能
- 序列化/反序列化
- 边界情况

运行: uv run pytest tests/modules/types/test_intent.py -v
"""

import time

import pytest

from src.modules.types import Intent, SourceContext
from src.modules.types import ActionType, DecisionMetadata, EmotionType, IntentAction, ParserType

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

    def test_intent_action_repr(self):
        """测试 IntentAction 的字符串表示"""
        action = IntentAction(type=ActionType.WAVE, params={"intensity": 0.8}, priority=70)

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
            decision_metadata=DecisionMetadata(
                parser_type=ParserType.LLM,
                extra={"confidence": 0.95},
            ),
        )
        assert intent.original_text == "测试"
        assert intent.response_text == "回复"
        assert intent.emotion == EmotionType.HAPPY
        assert len(intent.actions) == 2
        assert intent.source_context.source == "console_input"
        assert intent.decision_metadata.parser_type == ParserType.LLM
        assert intent.decision_metadata.extra["confidence"] == 0.95

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

    def test_intent_repr_short_text(self):
        """测试短文本的 repr"""
        intent = Intent(original_text="short", response_text="reply", emotion=EmotionType.SAD, actions=[])

        repr_str = repr(intent)

        # Pydantic BaseModel 的 repr 会显示完整的枚举类型
        assert "SAD" in repr_str or "sad" in repr_str
        assert "0" in repr_str  # actions 数量
        assert "id=" in repr_str  # 包含 id

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

    def test_create_intent_with_none_metadata(self):
        """测试 decision_metadata 为 None 时使用默认值"""
        # Pydantic BaseModel 使用 default=None，不传时会使用 None
        intent = Intent(
            original_text="test",
            response_text="response",
            emotion=EmotionType.NEUTRAL,
            actions=[],
        )

        assert intent.decision_metadata is None

    def test_create_intent_metadata_isolation(self):
        """测试 decision_metadata 的隔离（model_dump 返回副本）"""
        original_metadata = DecisionMetadata(
            parser_type=ParserType.LLM,
            extra={"key": "value"},
        )
        intent = Intent(
            original_text="test",
            response_text="response",
            emotion=EmotionType.NEUTRAL,
            actions=[],
            decision_metadata=original_metadata,
        )

        # Pydantic 默认不会复制嵌套对象，它们会共享引用
        # 如果需要隔离，应该使用 model_dump() 获取副本
        data = intent.model_dump()

        # 修改返回的字典
        data["decision_metadata"]["extra"]["new_key"] = "new_value"

        # 原始 intent 的 decision_metadata 不应被修改
        assert "new_key" not in intent.decision_metadata.extra

    def test_create_intent_empty_actions(self):
        """测试空动作列表"""
        intent = Intent(
            original_text="test", response_text="response", emotion=EmotionType.NEUTRAL, actions=[]
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
            intent = Intent(original_text="test", response_text="response", emotion=emotion, actions=[])
            assert intent.emotion == emotion

    def test_from_dict_with_default_values(self):
        """测试从字典创建 Intent 时使用默认值（使用 model_validate）"""
        data = {
            "original_text": "test",
            "response_text": "response",
        }

        intent = Intent.model_validate(data)

        assert intent.original_text == "test"
        assert intent.response_text == "response"
        assert intent.emotion == EmotionType.NEUTRAL
        assert intent.actions == []
        assert intent.decision_metadata is None
        assert isinstance(intent.timestamp, float)

    def test_from_dict_missing_actions(self):
        """测试从字典创建 Intent 时缺少 actions"""
        data = {"original_text": "test", "response_text": "response", "emotion": "sad"}

        intent = Intent.model_validate(data)

        assert intent.actions == []

    def test_from_dict_missing_priority(self):
        """测试从字典创建 Intent 时动作缺少 priority"""
        data = {
            "original_text": "test",
            "response_text": "response",
            "emotion": "neutral",
            "actions": [{"type": "blink", "params": {}}],
        }

        intent = Intent.model_validate(data)

        assert intent.actions[0].priority == 50  # 默认值

    def test_from_dict_invalid_emotion(self):
        """测试无效的情感类型"""
        from pydantic import ValidationError

        data = {"original_text": "test", "response_text": "response", "emotion": "invalid_emotion", "actions": []}

        with pytest.raises(ValidationError):
            Intent.model_validate(data)

    def test_from_dict_invalid_action_type(self):
        """测试无效的动作类型"""
        from pydantic import ValidationError

        data = {
            "original_text": "test",
            "response_text": "response",
            "emotion": "neutral",
            "actions": [{"type": "invalid_action", "params": {}}],
        }

        with pytest.raises(ValidationError):
            Intent.model_validate(data)


# =============================================================================
# 边界情况测试
# =============================================================================


class TestIntentEdgeCases:
    """测试 Intent 边界情况"""

    def test_empty_text(self):
        """测试空文本"""
        intent = Intent(original_text="", response_text="", emotion=EmotionType.NEUTRAL, actions=[])

        assert intent.original_text == ""
        assert intent.response_text == ""

    def test_very_long_text(self):
        """测试超长文本"""
        long_text = "测试" * 10000
        intent = Intent(
            original_text=long_text, response_text=long_text, emotion=EmotionType.NEUTRAL, actions=[]
        )

        assert intent.original_text == long_text
        assert intent.response_text == long_text

    def test_special_characters_in_text(self):
        """测试文本中的特殊字符"""
        special_text = "测试\n换行\t制表符\r回车\"引号'单引号\\反斜杠"
        intent = Intent(
            original_text=special_text, response_text=special_text, emotion=EmotionType.NEUTRAL, actions=[]
        )

        assert intent.original_text == special_text
        assert intent.response_text == special_text

    def test_unicode_emoji(self):
        """测试 Unicode emoji"""
        intent = Intent(
            original_text="😀😃😄😁😆😅🤣😂",
            response_text="❤️💕💖💗💓💝",
            emotion=EmotionType.LOVE,
            actions=[IntentAction(type=ActionType.EMOJI, params={"emoji": "😀"}, priority=50)],
        )

        assert "😀" in intent.original_text
        assert "❤️" in intent.response_text

    def test_empty_params_in_action(self):
        """测试动作中空 params"""
        action = IntentAction(type=ActionType.BLINK, params={}, priority=30)

        assert action.params == {}

    def test_complex_nested_params(self):
        """测试复杂的嵌套 params"""
        complex_params = {"nested": {"deep": {"value": 123, "list": [1, 2, 3]}}, "array": [{"a": 1}, {"b": 2}]}

        action = IntentAction(type=ActionType.EXPRESSION, params=complex_params, priority=70)

        assert action.params == complex_params

    def test_many_actions(self):
        """测试大量动作"""
        actions = [IntentAction(type=ActionType.BLINK, params={"index": i}, priority=i) for i in range(100)]

        intent = Intent(
            original_text="test", response_text="response", emotion=EmotionType.NEUTRAL, actions=actions
        )

        assert len(intent.actions) == 100
        assert intent.actions[0].priority == 0
        assert intent.actions[99].priority == 99

    def test_metadata_with_various_types(self):
        """测试 decision_metadata 包含各种数据类型"""
        decision_metadata = DecisionMetadata(
            parser_type=ParserType.LLM,
            llm_model="gpt-4",
            extra={
                "string": "value",
                "int": 42,
                "float": 3.14,
                "bool": True,
                "null": None,
                "list": [1, 2, 3],
                "dict": {"nested": "value"},
            },
        )

        intent = Intent(
            original_text="test", response_text="response", emotion=EmotionType.NEUTRAL, actions=[], decision_metadata=decision_metadata
        )

        assert intent.decision_metadata.parser_type == ParserType.LLM
        assert intent.decision_metadata.llm_model == "gpt-4"
        assert intent.decision_metadata.extra["string"] == "value"


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
            decision_metadata=DecisionMetadata(
                parser_type=ParserType.LLM,
                extra={"gift_value": 100},
            ),
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
        assert restored.decision_metadata.parser_type == ParserType.LLM
        assert restored.decision_metadata.extra["gift_value"] == 100

    def test_to_dict_complex_actions(self):
        """测试包含复杂动作的 Intent 转字典（使用 model_dump）"""
        intent = Intent(
            original_text="test",
            response_text="response",
            emotion=EmotionType.NEUTRAL,
            actions=[
                IntentAction(type=ActionType.EXPRESSION, params={"name": "smile"}, priority=70),
                IntentAction(type=ActionType.EMOJI, params={"emoji": "😀"}, priority=80),
                IntentAction(type=ActionType.WAVE, params={"intensity": 0.9}, priority=60),
            ],
        )

        result = intent.model_dump()

        assert len(result["actions"]) == 3
        assert result["actions"][0]["type"] == ActionType.EXPRESSION
        assert result["actions"][0]["params"]["name"] == "smile"
        assert result["actions"][1]["type"] == ActionType.EMOJI
        assert result["actions"][1]["params"]["emoji"] == "😀"
        assert result["actions"][2]["type"] == ActionType.WAVE
        assert result["actions"][2]["params"]["intensity"] == 0.9

    def test_to_dict_metadata_copy(self):
        """测试 model_dump 时 decision_metadata 被复制"""
        intent = Intent(
            original_text="test",
            response_text="response",
            emotion=EmotionType.NEUTRAL,
            actions=[],
            decision_metadata=DecisionMetadata(
                parser_type=ParserType.LLM,
                extra={"key": "value"},
            ),
        )

        result = intent.model_dump()

        # 修改返回的字典
        result["decision_metadata"]["extra"]["new_key"] = "new_value"

        # 原始 intent 的 decision_metadata 不应被修改
        assert "new_key" not in intent.decision_metadata.extra


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
