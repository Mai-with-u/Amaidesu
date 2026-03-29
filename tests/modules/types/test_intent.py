"""
Intent 类型单元测试

测试跨域共享的 Intent 相关类型（新版自然语言架构）：
- IntentMetadata: 意图元数据
- Intent: 决策意图（emotion/action/speech/context/metadata/structured_params）
- EmotionType: 情感类型（字符串别名）
- ActionType: 动作类型（字符串别名）

运行: uv run pytest tests/modules/types/test_intent.py -v
"""

import time

import pytest

from src.modules.types import (
    ActionType,
    EmotionType,
    Intent,
    IntentMetadata,
)


# =============================================================================
# EmotionType 和 ActionType 测试（字符串类型别名）
# =============================================================================


class TestEmotionTypeAlias:
    """测试 EmotionType 字符串类型别名"""

    def test_emotion_type_is_string(self):
        """测试 EmotionType 就是 str 类型"""
        emotion: EmotionType = "neutral"
        assert isinstance(emotion, str)
        assert emotion == "neutral"

    def test_emotion_type_various_values(self):
        """测试各种情感字符串值"""
        emotions = ["neutral", "happy", "sad", "angry", "surprised", "love", "shy", "excited", "confused", "scared"]
        for e in emotions:
            emotion: EmotionType = e
            assert emotion == e

    def test_emotion_type_assignment(self):
        """测试 EmotionType 赋值"""
        emotion: EmotionType = "happy"
        assert emotion == "happy"

    def test_emotion_type_comparison(self):
        """测试情感类型比较"""
        e1: EmotionType = "happy"
        e2: EmotionType = "happy"
        e3: EmotionType = "sad"
        assert e1 == e2
        assert e1 != e3


class TestActionTypeAlias:
    """测试 ActionType 字符串类型别名"""

    def test_action_type_is_string(self):
        """测试 ActionType 就是 str 类型"""
        action: ActionType = "blink"
        assert isinstance(action, str)
        assert action == "blink"

    def test_action_type_various_values(self):
        """测试各种动作字符串值"""
        actions = [
            "expression",
            "hotkey",
            "emoji",
            "blink",
            "nod",
            "shake",
            "wave",
            "clap",
            "sticker",
            "motion",
            "custom",
            "game_action",
            "none",
        ]
        for a in actions:
            action: ActionType = a
            assert action == a

    def test_action_type_assignment(self):
        """测试 ActionType 赋值"""
        action: ActionType = "wave"
        assert action == "wave"

    def test_action_type_comparison(self):
        """测试动作类型比较"""
        a1: ActionType = "blink"
        a2: ActionType = "blink"
        a3: ActionType = "wave"
        assert a1 == a2
        assert a1 != a3


# =============================================================================
# IntentMetadata 测试
# =============================================================================


class TestIntentMetadata:
    """测试 IntentMetadata 类"""

    def test_intent_metadata_creation_minimal(self):
        """测试创建最小 IntentMetadata"""
        metadata = IntentMetadata(
            source_id="console_input",
            decision_time=1700000000000,
        )
        assert metadata.source_id == "console_input"
        assert metadata.decision_time == 1700000000000
        assert metadata.parser_type is None
        assert metadata.llm_model is None
        assert metadata.replay_count is None
        assert metadata.extra == {}

    def test_intent_metadata_creation_full(self):
        """测试创建完整 IntentMetadata"""
        metadata = IntentMetadata(
            source_id="bili_danmaku",
            decision_time=1700000000000,
            parser_type="llm",
            llm_model="gpt-4",
            replay_count=0,
            extra={"confidence": 0.95},
        )
        assert metadata.source_id == "bili_danmaku"
        assert metadata.decision_time == 1700000000000
        assert metadata.parser_type == "llm"
        assert metadata.llm_model == "gpt-4"
        assert metadata.replay_count == 0
        assert metadata.extra["confidence"] == 0.95

    def test_intent_metadata_extra_default(self):
        """测试 extra 默认为空字典"""
        metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
        assert metadata.extra == {}

    def test_intent_metadata_extra_modification(self):
        """测试 extra 可修改"""
        metadata = IntentMetadata(
            source_id="test",
            decision_time=1700000000000,
            extra={"key": "value"},
        )
        metadata.extra["new_key"] = "new_value"
        assert metadata.extra["key"] == "value"
        assert metadata.extra["new_key"] == "new_value"

    def test_intent_metadata_serialization(self):
        """测试序列化"""
        metadata = IntentMetadata(
            source_id="console_input",
            decision_time=1700000000000,
            parser_type="rule",
            extra={"test": True},
        )
        data = metadata.model_dump()
        assert data["source_id"] == "console_input"
        assert data["decision_time"] == 1700000000000
        assert data["parser_type"] == "rule"
        assert data["extra"]["test"] is True

    def test_intent_metadata_deserialization(self):
        """测试反序列化"""
        data = {
            "source_id": "bili_danmaku",
            "decision_time": 1700000000000,
            "parser_type": "llm",
            "llm_model": "gpt-3.5",
            "replay_count": 1,
            "extra": {"score": 0.8},
        }
        metadata = IntentMetadata.model_validate(data)
        assert metadata.source_id == "bili_danmaku"
        assert metadata.decision_time == 1700000000000
        assert metadata.parser_type == "llm"
        assert metadata.llm_model == "gpt-3.5"
        assert metadata.replay_count == 1
        assert metadata.extra["score"] == 0.8

    def test_intent_metadata_missing_required_field(self):
        """测试缺少必填字段"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            IntentMetadata(
                source_id="test",
                # missing decision_time
            )

    def test_intent_metadata_repr(self):
        """测试 __repr__ 方法"""
        metadata = IntentMetadata(
            source_id="test_source",
            decision_time=1700000000000,
        )
        repr_str = repr(metadata)
        assert "IntentMetadata" in repr_str
        assert "test_source" in repr_str


# =============================================================================
# Intent 测试
# =============================================================================


class TestIntent:
    """测试 Intent 类"""

    def test_intent_creation_minimal(self):
        """测试创建最小 Intent"""
        metadata = IntentMetadata(source_id="console_input", decision_time=1700000000000)
        intent = Intent(
            metadata=metadata,
        )
        assert intent.emotion is None
        assert intent.action is None
        assert intent.speech is None
        assert intent.context is None
        assert intent.metadata.source_id == "console_input"
        assert intent.structured_params == {}

    def test_intent_creation_full(self):
        """测试创建完整 Intent"""
        metadata = IntentMetadata(
            source_id="bili_danmaku",
            decision_time=1700000000000,
            parser_type="llm",
            llm_model="gpt-4",
        )
        intent = Intent(
            emotion="害羞",
            action="脸红并挥手",
            speech="大家好呀~",
            context="用户进入直播间",
            metadata=metadata,
            structured_params={
                "tts": {"voice": "shy", "speed": 1.0},
                "vts": {"expression": "shy"},
            },
        )
        assert intent.emotion == "害羞"
        assert intent.action == "脸红并挥手"
        assert intent.speech == "大家好呀~"
        assert intent.context == "用户进入直播间"
        assert intent.metadata.source_id == "bili_danmaku"
        assert intent.metadata.parser_type == "llm"
        assert intent.metadata.llm_model == "gpt-4"
        assert "tts" in intent.structured_params
        assert "vts" in intent.structured_params

    def test_intent_emotion_as_string(self):
        """测试 emotion 字段为字符串"""
        metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
        intent = Intent(
            emotion="happy",
            metadata=metadata,
        )
        assert intent.emotion == "happy"
        assert isinstance(intent.emotion, str)

    def test_intent_action_as_string(self):
        """测试 action 字段为字符串"""
        metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
        intent = Intent(
            action="wave",
            metadata=metadata,
        )
        assert intent.action == "wave"
        assert isinstance(intent.action, str)

    def test_intent_speech(self):
        """测试 speech 字段"""
        metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
        intent = Intent(
            speech="今天天气真不错！",
            metadata=metadata,
        )
        assert intent.speech == "今天天气真不错！"

    def test_intent_context(self):
        """测试 context 字段"""
        metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
        intent = Intent(
            context="回复用户问题",
            metadata=metadata,
        )
        assert intent.context == "回复用户问题"

    def test_intent_structured_params_default(self):
        """测试 structured_params 默认为空字典"""
        metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
        intent = Intent(metadata=metadata)
        assert intent.structured_params == {}

    def test_intent_structured_params_single_provider(self):
        """测试 structured_params 单个 provider"""
        metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
        intent = Intent(
            metadata=metadata,
            structured_params={
                "tts": {"voice": "happy", "speed": 1.2},
            },
        )
        assert "tts" in intent.structured_params
        assert intent.structured_params["tts"]["voice"] == "happy"
        assert intent.structured_params["tts"]["speed"] == 1.2

    def test_intent_structured_params_multiple_providers(self):
        """测试 structured_params 多个 providers"""
        metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
        intent = Intent(
            metadata=metadata,
            structured_params={
                "tts": {"voice": "excited", "speed": 1.5},
                "vts": {"expression": "excited", "motion": "jump"},
                "subtitle": {"font_size": 24, "color": "white"},
            },
        )
        assert len(intent.structured_params) == 3
        assert intent.structured_params["tts"]["voice"] == "excited"
        assert intent.structured_params["vts"]["expression"] == "excited"
        assert intent.structured_params["subtitle"]["font_size"] == 24

    def test_intent_structured_params_common_key(self):
        """测试 structured_params 的 common key（共享参数）"""
        metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
        intent = Intent(
            metadata=metadata,
            structured_params={
                "common": {"emotion_en": "happy", "action_en": "wave"},
                "tts": {"voice": "happy"},
            },
        )
        assert "common" in intent.structured_params
        assert intent.structured_params["common"]["emotion_en"] == "happy"
        assert intent.structured_params["common"]["action_en"] == "wave"

    def test_intent_serialization(self):
        """测试序列化"""
        metadata = IntentMetadata(
            source_id="console_input",
            decision_time=1700000000000,
            parser_type="rule",
        )
        intent = Intent(
            emotion="shy",
            action="脸红",
            speech="你好呀~",
            context="问候",
            metadata=metadata,
            structured_params={"tts": {"voice": "shy"}},
        )
        data = intent.model_dump()
        assert data["emotion"] == "shy"
        assert data["action"] == "脸红"
        assert data["speech"] == "你好呀~"
        assert data["context"] == "问候"
        assert data["metadata"]["source_id"] == "console_input"
        assert data["structured_params"]["tts"]["voice"] == "shy"

    def test_intent_deserialization(self):
        """测试反序列化"""
        data = {
            "emotion": "happy",
            "action": "挥手",
            "speech": "很高兴见到你！",
            "context": "用户问候",
            "metadata": {
                "source_id": "bili_danmaku",
                "decision_time": 1700000000000,
                "parser_type": "llm",
            },
            "structured_params": {
                "vts": {"expression": "happy"},
            },
        }
        intent = Intent.model_validate(data)
        assert intent.emotion == "happy"
        assert intent.action == "挥手"
        assert intent.speech == "很高兴见到你！"
        assert intent.context == "用户问候"
        assert intent.metadata.source_id == "bili_danmaku"
        assert intent.metadata.parser_type == "llm"
        assert intent.structured_params["vts"]["expression"] == "happy"

    def test_intent_missing_metadata(self):
        """测试缺少必填字段 metadata"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Intent(
                emotion="happy",
                speech="test",
                # missing metadata
            )

    def test_intent_repr(self):
        """测试 __repr__ 方法"""
        metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
        intent = Intent(
            emotion="害羞",
            speech="你好呀~",
            metadata=metadata,
        )
        repr_str = repr(intent)
        assert "Intent(" in repr_str
        assert "害羞" in repr_str

    def test_intent_json_schema(self):
        """测试 JSON Schema 生成"""
        schema = Intent.model_json_schema()
        assert "properties" in schema
        assert "emotion" in schema["properties"]
        assert "action" in schema["properties"]
        assert "speech" in schema["properties"]
        assert "context" in schema["properties"]
        assert "metadata" in schema["properties"]
        assert "structured_params" in schema["properties"]


# =============================================================================
# 边界情况测试
# =============================================================================


class TestIntentEdgeCases:
    """测试 Intent 边界情况"""

    def test_empty_strings(self):
        """测试空字符串字段"""
        metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
        intent = Intent(
            emotion="",
            action="",
            speech="",
            context="",
            metadata=metadata,
        )
        assert intent.emotion == ""
        assert intent.action == ""
        assert intent.speech == ""
        assert intent.context == ""

    def test_very_long_text(self):
        """测试超长文本"""
        long_text = "测试" * 10000
        metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
        intent = Intent(
            speech=long_text,
            metadata=metadata,
        )
        assert intent.speech == long_text

    def test_special_characters(self):
        """测试特殊字符"""
        special_text = "测试\n换行\t制表符\r回车\"引号'单引号\\反斜杠"
        metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
        intent = Intent(
            speech=special_text,
            metadata=metadata,
        )
        assert intent.speech == special_text

    def test_unicode_emoji(self):
        """测试 Unicode emoji"""
        metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
        intent = Intent(
            emotion="开心",
            speech="😀😃😄😁😆",
            metadata=metadata,
            structured_params={"tts": {"emoji": "😀"}},
        )
        assert "😀" in intent.speech
        assert intent.structured_params["tts"]["emoji"] == "😀"

    def test_empty_structured_params(self):
        """测试空的 structured_params"""
        metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
        intent = Intent(
            metadata=metadata,
            structured_params={},
        )
        assert intent.structured_params == {}

    def test_structured_params_complex_values(self):
        """测试 structured_params 复杂值"""
        metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
        intent = Intent(
            metadata=metadata,
            structured_params={
                "tts": {
                    "voice": "happy",
                    "speed": 1.5,
                    "pitch": 1.2,
                    "nested": {"deep": {"value": 123, "list": [1, 2, 3]}},
                },
                "vts": {
                    "expressions": ["happy", "surprised"],
                    "settings": {"intensity": 0.8, "enabled": True},
                },
            },
        )
        assert intent.structured_params["tts"]["speed"] == 1.5
        assert intent.structured_params["tts"]["nested"]["deep"]["value"] == 123
        assert "happy" in intent.structured_params["vts"]["expressions"]
        assert intent.structured_params["vts"]["settings"]["enabled"] is True

    def test_metadata_with_various_extra_types(self):
        """测试 metadata.extra 包含各种数据类型"""
        metadata = IntentMetadata(
            source_id="test",
            decision_time=1700000000000,
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
        assert metadata.extra["string"] == "value"
        assert metadata.extra["int"] == 42
        assert metadata.extra["float"] == 3.14
        assert metadata.extra["bool"] is True
        assert metadata.extra["null"] is None
        assert metadata.extra["list"] == [1, 2, 3]
        assert metadata.extra["dict"]["nested"] == "value"

    def test_intent_replay_count(self):
        """测试 replay_count 字段"""
        metadata = IntentMetadata(
            source_id="test",
            decision_time=1700000000000,
            replay_count=3,
        )
        assert metadata.replay_count == 3
        intent = Intent(metadata=metadata)
        assert intent.metadata.replay_count == 3


# =============================================================================
# 集成测试
# =============================================================================


class TestIntentIntegration:
    """Intent 集成测试"""

    def test_intent_full_workflow(self):
        """测试完整工作流"""
        # 1. 创建 metadata
        metadata = IntentMetadata(
            source_id="bili_danmaku",
            decision_time=int(time.time() * 1000),
            parser_type="llm",
            llm_model="gpt-4",
            replay_count=0,
            extra={"user_name": "测试用户"},
        )

        # 2. 创建 Intent
        intent = Intent(
            emotion="开心",
            action="挥手",
            speech="欢迎来到直播间！",
            context="用户进入",
            metadata=metadata,
            structured_params={
                "tts": {"voice": "welcoming", "speed": 1.0},
                "vts": {"expression": "happy", "motion": "wave"},
                "subtitle": {"text": "欢迎来到直播间！"},
            },
        )

        # 3. 验证
        assert intent.emotion == "开心"
        assert intent.action == "挥手"
        assert intent.speech == "欢迎来到直播间！"
        assert intent.metadata.parser_type == "llm"
        assert intent.structured_params["tts"]["voice"] == "welcoming"

    def test_intent_round_trip(self):
        """测试序列化-反序列化往返"""
        original = Intent(
            emotion="害羞",
            action="脸红",
            speech="谢谢大家~",
            context="收到礼物",
            metadata=IntentMetadata(
                source_id="bili_danmaku",
                decision_time=1700000000000,
                parser_type="llm",
                extra={"gift_name": "辣条"},
            ),
            structured_params={
                "tts": {"voice": "shy"},
                "vts": {"expression": "shy"},
            },
        )

        # 序列化
        data = original.model_dump()

        # 反序列化
        restored = Intent.model_validate(data)

        # 验证
        assert restored.emotion == original.emotion
        assert restored.action == original.action
        assert restored.speech == original.speech
        assert restored.context == original.context
        assert restored.metadata.source_id == original.metadata.source_id
        assert restored.metadata.parser_type == original.metadata.parser_type
        assert restored.structured_params["tts"]["voice"] == original.structured_params["tts"]["voice"]

    def test_intent_different_emotions_and_actions(self):
        """测试不同的情感和动作组合"""
        test_cases = [
            ("开心", "挥手", "tts"),
            ("害羞", "脸红", "vts"),
            ("惊讶", "睁大眼睛", "subtitle"),
            ("生气", "皱眉", "obs"),
            ("悲伤", "低头", "sticker"),
        ]

        for emotion, action, provider in test_cases:
            metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
            intent = Intent(
                emotion=emotion,
                action=action,
                speech=f"测试{emotion}",
                metadata=metadata,
                structured_params={provider: {}},
            )
            assert intent.emotion == emotion
            assert intent.action == action

    def test_intent_multiple_providers_params(self):
        """测试多个 provider 的参数"""
        metadata = IntentMetadata(source_id="test", decision_time=1700000000000)
        intent = Intent(
            emotion="兴奋",
            speech="好耶！",
            metadata=metadata,
            structured_params={
                "tts": {"voice": "excited", "speed": 1.5, "pitch": 1.3},
                "edge_tts": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+20%"},
                "vts": {"expression": "excited", "motion": "jump", "intensity": 0.9},
                "gptsovits": {"model": "happy", "seed": 42},
                "subtitle": {"font": "微软雅黑", "size": 24, "color": "#FFFFFF", "outline": True},
                "obs": {"source": "webcam", "filter": "blur"},
                "sticker": {"pack": "emoji", "id": "happy"},
            },
        )

        assert len(intent.structured_params) == 7
        assert intent.structured_params["tts"]["speed"] == 1.5
        assert intent.structured_params["edge_tts"]["voice"] == "zh-CN-XiaoxiaoNeural"
        assert intent.structured_params["vts"]["expression"] == "excited"
        assert intent.structured_params["gptsovits"]["seed"] == 42
        assert intent.structured_params["subtitle"]["size"] == 24
        assert intent.structured_params["obs"]["filter"] == "blur"
        assert intent.structured_params["sticker"]["id"] == "happy"


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
