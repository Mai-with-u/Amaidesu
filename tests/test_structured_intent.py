"""
Structured Intent 测试

测试 MaiBotDecider 的结构化 Intent 提取功能：
1. 结构化数据可用时 → 直接构造 Intent（无 LLM 调用)
2. 结构化数据缺失 → 警告 + 降级到 LLM 解析
3. structured_intent=false → 始终使用 LLM 解析
"""

import pytest
from unittest.mock import MagicMock
from typing import Any, Dict

from maim_message import MessageBase, BaseMessageInfo, FormatInfo, Seg, UserInfo, GroupInfo

from src.domains.decision.providers.maibot.maibot_decider import MaiBotDecider
from src.modules.types import Intent


def create_message_base(
    text: str = "测试消息",
    additional_config: Dict[str, Any] = None,
    seg_type: str = "text",
) -> MessageBase:
    """创建测试用 MessageBase"""
    user_info = UserInfo(
        platform="amaidesu",
        user_id="test_user",
        user_nickname="测试用户",
    )
    format_info = FormatInfo(
        content_format=["text"],
        accept_format=["text"],
    )
    group_info = GroupInfo(
        platform="amaidesu",
        group_id="live_room",
        group_name="直播间",
    )
    message_id = f"test_{int(1234567890.0)}"
    config = additional_config if additional_config is not None else {}

    return MessageBase(
        message_info=BaseMessageInfo(
            message_id=message_id,
            platform="amaidesu",
            user_info=user_info,
            time=1234567890.0,
            format_info=format_info,
            additional_config=config,
            group_info=group_info,
        ),
        message_segment=Seg(type=seg_type, data=text),
        raw_message=text,
    )


def create_maibot_decider(
    structured_intent: bool = True,
    llm_service: MagicMock = None,
    prompt_service: MagicMock = None,
) -> MaiBotDecider:
    """创建配置好的 MaiBotDecider 测试实例"""
    config: Dict[str, Any] = {
        "host": "localhost",
        "port": 8000,
        "platform": "amaidesu",
        "structured_intent": structured_intent,
    }
    event_bus = MagicMock()
    decider = MaiBotDecider(config=config, event_bus=event_bus, llm_service=llm_service, prompt_service=prompt_service)
    decider.logger = MagicMock()
    return decider


class TestExtractStructuredIntent:
    """测试 _extract_structured_intent 方法"""

    def test_extract_with_emotion_and_action(self):
        """结构化数据完整时正确提取"""
        decider = create_maibot_decider()

        response = create_message_base(
            text="谢谢大家！",
            additional_config={"emotion": "开心", "action": "比心"},
        )

        result = decider._extract_structured_intent(response)

        assert result is not None
        emotion, action = result
        assert emotion == "开心"
        assert action == "比心"

    def test_extract_with_only_emotion(self):
        """只有 emotion 时正确提取"""
        decider = create_maibot_decider()

        response = create_message_base(
            text="你好呀",
            additional_config={"emotion": "开心"},
        )

        result = decider._extract_structured_intent(response)

        assert result is not None
        emotion, action = result
        assert emotion == "开心"
        assert action is None

    def test_extract_with_only_action(self):
        """只有 action 时正确提取"""
        decider = create_maibot_decider()

        response = create_message_base(
            text="好的",
            additional_config={"action": "点头"},
        )

        result = decider._extract_structured_intent(response)

        assert result is not None
        emotion, action = result
        assert emotion is None
        assert action == "点头"

    def test_extract_returns_none_when_no_structured_data(self):
        """无结构化数据时返回 None"""
        decider = create_maibot_decider()

        response = create_message_base(
            text="普通消息",
            additional_config={},
        )

        result = decider._extract_structured_intent(response)

        assert result is None

    def test_extract_returns_none_when_additional_config_is_empty_dict(self):
        """additional_config 为空字典时返回 None"""
        decider = create_maibot_decider()

        response = create_message_base(
            text="普通消息",
            additional_config={},
        )

        result = decider._extract_structured_intent(response)

        assert result is None


class TestStructuredIntentFlow:
    """测试结构化 Intent 流程"""

    @pytest.mark.asyncio
    async def test_structured_data_available_no_llm_call(self):
        """结构化数据可用时跳过 LLM 调用"""
        mock_llm = MagicMock()
        mock_llm.chat_fast = MagicMock(side_effect=Exception("不应被调用"))

        decider = create_maibot_decider(structured_intent=True, llm_service=mock_llm)

        response = create_message_base(
            text="谢谢大家的支持！",
            additional_config={"emotion": "感动", "action": "比心"},
        )

        intent = decider._parse_intent_from_maibot_response(response)

        assert isinstance(intent, Intent)
        assert intent.emotion == "感动"
        assert intent.action == "比心"
        assert intent.speech == "谢谢大家的支持！"
        assert intent.metadata.parser_type == "structured"
        mock_llm.chat_fast.assert_not_called()

    @pytest.mark.asyncio
    async def test_structured_data_missing_warns_and_falls_back_to_llm(self):
        """结构化数据缺失时警告并降级到 LLM"""
        mock_llm = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.content = '{"emotion": "开心", "action": "笑", "speech": "哈哈"}'
        mock_result.model = "test-model"
        mock_llm.chat_fast = MagicMock(return_value=mock_result)

        mock_prompt = MagicMock()
        mock_prompt.render = MagicMock(return_value="mock prompt")

        decider = create_maibot_decider(
            structured_intent=True,
            llm_service=mock_llm,
            prompt_service=mock_prompt,
        )

        response = create_message_base(
            text="哈哈，太好玩了！",
            additional_config={},
        )

        intent = decider._parse_intent_from_maibot_response(response)

        assert isinstance(intent, Intent)
        assert intent.emotion == "开心"
        assert intent.action == "笑"
        decider.logger.warning.assert_called()
        mock_llm.chat_fast.assert_called_once()

    @pytest.mark.asyncio
    async def test_structured_intent_false_skips_structured_extraction(self):
        """structured_intent=false 时跳过结构化提取"""
        mock_llm = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.content = '{"emotion": "开心", "action": "笑", "speech": "哈哈"}'
        mock_result.model = "test-model"
        mock_llm.chat_fast = MagicMock(return_value=mock_result)

        mock_prompt = MagicMock()
        mock_prompt.render = MagicMock(return_value="mock prompt")

        decider = create_maibot_decider(
            structured_intent=False,
            llm_service=mock_llm,
            prompt_service=mock_prompt,
        )

        response = create_message_base(
            text="哈哈，太好玩了！",
            additional_config={"emotion": "感动", "action": "比心"},
        )

        intent = decider._parse_intent_from_maibot_response(response)

        assert isinstance(intent, Intent)
        assert intent.emotion == "开心"
        assert intent.action == "笑"
        assert intent.metadata.parser_type == "llm"
        mock_llm.chat_fast.assert_called_once()


class TestConfigToggle:
    """测试配置开关"""

    def test_structured_intent_default_true(self):
        """structured_intent 默认为 True"""
        config: Dict[str, Any] = {
            "host": "localhost",
            "port": 8000,
            "platform": "amaidesu",
        }
        event_bus = MagicMock()
        decider = MaiBotDecider(config=config, event_bus=event_bus)

        assert decider.typed_config.structured_intent is True

    def test_structured_intent_can_be_set_false(self):
        """structured_intent 可以设置为 False"""
        config: Dict[str, Any] = {
            "host": "localhost",
            "port": 8000,
            "platform": "amaidesu",
            "structured_intent": False,
        }
        event_bus = MagicMock()
        decider = MaiBotDecider(config=config, event_bus=event_bus)

        assert decider.typed_config.structured_intent is False

    def test_structured_intent_can_be_set_true_explicitly(self):
        """structured_intent 可以显式设置为 True"""
        config: Dict[str, Any] = {
            "host": "localhost",
            "port": 8000,
            "platform": "amaidesu",
            "structured_intent": True,
        }
        event_bus = MagicMock()
        decider = MaiBotDecider(config=config, event_bus=event_bus)

        assert decider.typed_config.structured_intent is True


class TestStructuredIntentWithRealMessageBase:
    """使用真实 MessageBase 结构的测试"""

    def test_extract_from_real_message_dict(self):
        """从真实消息字典提取结构化数据"""
        decider = create_maibot_decider()

        message_dict = {
            "message_info": {
                "message_id": "msg_456",
                "platform": "amaidesu",
                "user_info": {
                    "platform": "amaidesu",
                    "user_id": "user_123",
                    "user_nickname": "测试用户",
                },
                "time": 1234567890.0,
                "additional_config": {
                    "emotion": "害羞",
                    "action": "脸红并挥手",
                    "source": "amaidesu_provider",
                },
            },
            "message_segment": {
                "type": "text",
                "data": "谢谢大家！",
            },
            "raw_message": "谢谢大家！",
        }

        message = MessageBase.from_dict(message_dict)
        result = decider._extract_structured_intent(message)

        assert result is not None
        emotion, action = result
        assert emotion == "害羞"
        assert action == "脸红并挥手"

    def test_intent_construction_with_structured_data(self):
        """使用结构化数据构造 Intent"""
        decider = create_maibot_decider()

        message_dict = {
            "message_info": {
                "message_id": "msg_789",
                "platform": "amaidesu",
                "user_info": {
                    "platform": "amaidesu",
                    "user_id": "user_456",
                    "user_nickname": "观众",
                },
                "time": 1234567890.0,
                "additional_config": {
                    "emotion": "惊喜",
                    "action": "欢呼",
                },
            },
            "message_segment": {
                "type": "text",
                "data": "哇！真的吗？太棒了！",
            },
            "raw_message": "哇！真的吗？太棒了！",
        }

        message = MessageBase.from_dict(message_dict)
        intent = decider._parse_intent_from_maibot_response(message)

        assert isinstance(intent, Intent)
        assert intent.emotion == "惊喜"
        assert intent.action == "欢呼"
        assert intent.speech == "哇！真的吗？太棒了！"
        assert intent.metadata.parser_type == "structured"
        assert intent.metadata.source_id == "maibot"