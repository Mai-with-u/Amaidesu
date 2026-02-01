"""
Understanding层单元测试
"""

import pytest
from src.understanding.intent import Intent, EmotionType, ActionType, IntentAction
from src.understanding.response_parser import ResponseParser
from maim_message import MessageBase, BaseMessageInfo, UserInfo, Seg, FormatInfo


@pytest.fixture
def sample_message():
    """创建示例MessageBase"""
    user_info = UserInfo(user_id="test_user", nickname="Test User")
    format_info = FormatInfo(font=None, color=None, size=None)
    seg = Seg(type="text", data="你好", format=format_info)

    return MessageBase(
        message_info=BaseMessageInfo(
            message_id="test_001",
            platform="test",
            sender=user_info,
            timestamp=1234567890.0,
        ),
        message_segment=seg,
    )


@pytest.fixture
def response_parser():
    """创建ResponseParser实例"""
    return ResponseParser()


def test_intent_creation():
    """测试Intent创建"""
    emotion = EmotionType.HAPPY
    action = IntentAction(type=ActionType.TEXT, params={"text": "测试"})

    intent = Intent(
        original_text="测试文本",
        emotion=emotion,
        response_text="这是响应",
        actions=[action],
        metadata={"key": "value"},
    )

    assert intent.original_text == "测试文本"
    assert intent.emotion == EmotionType.HAPPY
    assert len(intent.actions) == 1
    assert intent.actions[0].type == ActionType.TEXT


def test_emotion_type_enum():
    """测试EmotionType枚举"""
    assert EmotionType.HAPPY == "happy"
    assert EmotionType.SAD == "sad"
    assert EmotionType.NEUTRAL == "neutral"


def test_intent_action_creation():
    """测试IntentAction创建"""
    action1 = IntentAction(type=ActionType.TEXT, params={"text": "你好", "speed": 1.0})
    assert action1.type == ActionType.TEXT
    assert action1.params["text"] == "你好"

    action2 = IntentAction(type=ActionType.EXPRESSION, params={"emotion": "smile"})
    assert action2.type == ActionType.EXPRESSION
    assert action2.params["emotion"] == "smile"


def test_response_parser_extract_text(response_parser, sample_message):
    """测试提取文本"""
    text = response_parser._extract_text(sample_message)
    assert text == "你好"


def test_response_parser_extract_response_text(response_parser, sample_message):
    """测试提取响应文本"""
    # 创建包含响应文本的MessageBase
    user_info = UserInfo(user_id="test_user", nickname="Test User")
    format_info = FormatInfo(font=None, color=None, size=None)
    seg = Seg(
        type="text",
        data="这是LLM生成的响应",
        format=format_info,
    )

    message = MessageBase(
        message_info=BaseMessageInfo(
            message_id="test_002",
            platform="test",
            sender=user_info,
            timestamp=1234567890.0,
        ),
        message_segment=seg,
    )

    response_text = response_parser._extract_response_text(message)
    assert response_text == "这是LLM生成的响应"


@pytest.mark.asyncio
async def test_response_parser_parse(response_parser, sample_message):
    """测试解析MessageBase为Intent"""
    intent = await response_parser.parse(sample_message)

    assert intent.original_text == "你好"
    assert intent.emotion is not None
    assert isinstance(intent.emotion, EmotionType)
    assert len(intent.actions) >= 0
    assert "source" in intent.metadata


def test_response_parser_emotion_recognition(response_parser):
    """测试情感识别"""
    # 基于规则的情感识别
    emotion1 = response_parser._recognize_emotion("哈哈，太搞笑了")
    assert emotion1 == EmotionType.HAPPY

    emotion2 = response_parser._recognize_emotion("我很难过")
    assert emotion2 == EmotionType.SAD

    emotion3 = response_parser._recognize_emotion("你真是让我生气")
    assert emotion3 == EmotionType.ANGRY


def test_response_parser_action_extraction(response_parser):
    """测试动作提取"""
    # 提取指令类动作
    text1 = "请说你好"
    intent1 = Intent(
        original_text=text1,
        emotion=EmotionType.NEUTRAL,
        response_text="",
        actions=[],
        metadata={},
    )
    actions1 = response_parser._extract_actions(text1, intent1)
    assert any(a.type == ActionType.TEXT for a in actions1)

    # 提取表情类动作
    text2 = "请笑一下"
    intent2 = Intent(
        original_text=text2,
        emotion=EmotionType.NEUTRAL,
        response_text="",
        actions=[],
        metadata={},
    )
    actions2 = response_parser._extract_actions(text2, intent2)
    assert any(a.type == ActionType.EXPRESSION for a in actions2)


def test_canonical_message_from_message_base():
    """测试从MessageBase创建CanonicalMessage"""
    from src.canonical.canonical_message import MessageBuilder

    # 创建MessageBase
    user_info = UserInfo(user_id="test_user", nickname="Test User")
    format_info = FormatInfo(font=None, color=None, size=None)
    seg = Seg(type="text", data="测试消息", format=format_info)

    message = MessageBase(
        message_info=BaseMessageInfo(
            message_id="test_003",
            platform="test",
            sender=user_info,
            timestamp=1234567890.0,
        ),
        message_segment=seg,
    )

    # 转换为CanonicalMessage
    canonical_message = MessageBuilder.build_from_message_base(message)

    assert canonical_message.text == "测试消息"
    assert canonical_message.source == "test"
    assert canonical_message.metadata["user_id"] == "test_user"
    assert canonical_message.metadata["user_nickname"] == "Test User"
    assert canonical_message.original_message == message


def test_canonical_message_to_message_base():
    """测试将CanonicalMessage转换为MessageBase"""
    from src.canonical.canonical_message import CanonicalMessage

    # 创建CanonicalMessage
    canonical_message = CanonicalMessage(
        text="测试消息",
        source="test",
        metadata={"user_id": "test_user"},
    )

    # 转换为MessageBase
    message = canonical_message.to_message_base()

    assert message is not None
    assert message.message_info.platform == "test"
    assert message.message_info.sender.user_id == "test_user"
    assert message.message_segment.data == "测试消息"
