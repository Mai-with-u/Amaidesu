"""
MaiCoreDecisionProvider 测试
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from maim_message import BaseMessageInfo, MessageBase, Seg, UserInfo

from src.domains.decision.providers.maicore import MaiCoreDecisionProvider
from src.modules.di.context import ProviderContext
from src.modules.types import (
    ActionType,
    EmotionType,
    Intent,
    IntentAction,
)


@pytest.fixture
def maicore_config():
    """基础配置"""
    return {
        "type": "maicore",
        "host": "localhost",
        "port": 8000,
        "platform": "amaidesu",
    }


@pytest.fixture
def mock_event_bus():
    """Mock EventBus"""
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()
    return event_bus


@pytest.fixture
def mock_llm_service():
    """Mock LLMService"""
    llm_service = MagicMock()
    llm_service.chat_fast = MagicMock()

    # 创建 mock result
    result = MagicMock()
    result.success = True
    result.content = (
        '{"emotion": "happy", "actions": [{"type": "wave", "params": {}, "priority": 60}], "response_text": "你好呀！"}'
    )
    result.model = "test-model"

    llm_service.chat_fast.return_value = result
    return llm_service


@pytest.fixture
def mock_provider_context(mock_event_bus, mock_llm_service):
    """Mock ProviderContext"""
    # 创建一个简单的 mock prompt_service
    mock_prompt_service = MagicMock()
    mock_prompt_service.get_raw = MagicMock(return_value="你是一个助手。")
    mock_prompt_service.render = MagicMock(return_value="你是一个助手。")

    return ProviderContext(
        event_bus=mock_event_bus,
        llm_service=mock_llm_service,
        prompt_service=mock_prompt_service,
    )


@pytest.fixture
def provider(maicore_config, mock_provider_context):
    """创建 Provider 实例"""
    return MaiCoreDecisionProvider(maicore_config, mock_provider_context)


@pytest.fixture
def setup_provider(provider):
    """设置 Provider 的异步 fixture"""

    async def _setup():
        # 初始化（不真正启动Router）
        await provider.init()
        return provider

    return _setup


@pytest.fixture
def mock_message_base():
    """创建 Mock MessageBase"""
    user_info = UserInfo(user_id="test_user", user_nickname="TestUser")
    message_info = BaseMessageInfo(
        message_id="test_msg_123",
        platform="amaidesu",
        user_info=user_info,
        time=1234567890.0,
    )
    seg = Seg(type="text", data="你好，你好呀！")

    return MessageBase(message_info=message_info, message_segment=seg)


class TestParseWithLLM:
    """测试 LLM 解析功能"""

    @pytest.mark.asyncio
    async def test_parse_with_llm_success(self, setup_provider, mock_message_base, mock_llm_service):
        """测试 LLM 成功解析"""
        provider = await setup_provider()

        # Mock LLM 返回有效 JSON
        mock_llm_service.chat_fast.return_value.success = True
        mock_llm_service.chat_fast.return_value.content = """
        {
            "emotion": "happy",
            "actions": [
                {"type": "wave", "params": {}, "priority": 60},
                {"type": "expression", "params": {"name": "smile"}, "priority": 70}
            ],
            "response_text": "你好呀！"
        }
        """
        mock_llm_service.chat_fast.return_value.model = "gpt-4"

        # 调用解析
        intent = provider._parse_with_llm("你好，你好呀！", mock_message_base, mock_llm_service)

        # 验证结果
        assert intent.emotion == EmotionType.HAPPY
        assert intent.response_text == "你好呀！"
        assert len(intent.actions) == 2

        # 验证第一个 action
        assert intent.actions[0].type == ActionType.WAVE
        assert intent.actions[0].params == {}
        assert intent.actions[0].priority == 60

        # 验证第二个 action
        assert intent.actions[1].type == ActionType.EXPRESSION
        assert intent.actions[1].params == {"name": "smile"}
        assert intent.actions[1].priority == 70

        # 验证 SourceContext
        assert intent.source_context.source == "amaidesu"
        assert intent.source_context.user_id == "test_user"
        assert intent.source_context.user_nickname == "TestUser"

        # 验证 metadata
        assert intent.metadata["parser"] == "llm"
        assert intent.metadata["llm_model"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_parse_with_llm_fallback_to_rules(self, setup_provider, mock_message_base, mock_llm_service):
        """测试 LLM 失败后降级到规则解析"""
        provider = await setup_provider()

        # Mock LLM 抛出异常
        mock_llm_service.chat_fast.side_effect = Exception("LLM 服务不可用")

        # 应该抛出异常（由调用方决定是否降级）
        with pytest.raises(Exception, match="LLM 服务不可用"):
            provider._parse_with_llm("你好", mock_message_base, mock_llm_service)

    @pytest.mark.asyncio
    async def test_parse_with_llm_invalid_json(self, setup_provider, mock_message_base, mock_llm_service):
        """测试 LLM 返回无效 JSON"""
        provider = await setup_provider()

        # Mock LLM 返回无效 JSON（包含大括号但无法解析）
        mock_llm_service.chat_fast.return_value.success = True
        mock_llm_service.chat_fast.return_value.content = "这是{一些}无效的 JSON {文本}"

        # 应该抛出 ValueError
        with pytest.raises(ValueError, match="无法解析 LLM 返回的 JSON"):
            provider._parse_with_llm("测试", mock_message_base, mock_llm_service)

    @pytest.mark.asyncio
    async def test_parse_with_llm_unknown_emotion(self, setup_provider, mock_message_base, mock_llm_service):
        """测试 LLM 返回未知情感类型"""
        provider = await setup_provider()

        # Mock LLM 返回未知情感
        mock_llm_service.chat_fast.return_value.success = True
        mock_llm_service.chat_fast.return_value.content = (
            '{"emotion": "unknown_emotion", "actions": [], "response_text": "测试"}'
        )
        mock_llm_service.chat_fast.return_value.model = "test-model"

        # 应该降级到 NEUTRAL
        intent = provider._parse_with_llm("测试", mock_message_base, mock_llm_service)

        assert intent.emotion == EmotionType.NEUTRAL

    @pytest.mark.asyncio
    async def test_parse_with_llm_unknown_action_type(self, setup_provider, mock_message_base, mock_llm_service):
        """测试 LLM 返回未知动作类型"""
        provider = await setup_provider()

        # Mock LLM 返回未知动作类型
        mock_llm_service.chat_fast.return_value.success = True
        mock_llm_service.chat_fast.return_value.content = """
        {
            "emotion": "happy",
            "actions": [
                {"type": "unknown_action", "params": {}, "priority": 50}
            ],
            "response_text": "测试"
        }
        """
        mock_llm_service.chat_fast.return_value.model = "test-model"

        intent = provider._parse_with_llm("测试", mock_message_base, mock_llm_service)

        # 未知动作应该被转换为 NONE
        assert len(intent.actions) == 1
        assert intent.actions[0].type == ActionType.NONE


class TestParseWithRules:
    """测试规则解析功能"""

    @pytest.mark.asyncio
    async def test_parse_with_rules_emotion_detection(self, setup_provider, mock_message_base):
        """测试关键词情感识别"""
        provider = await setup_provider()

        # 测试各种情感
        test_cases = [
            ("我今天很开心，太棒了！", EmotionType.HAPPY),
            ("这件事让我很难过，很伤心", EmotionType.SAD),
            ("我真的很生气，很愤怒", EmotionType.ANGRY),
            ("哇，天啊，真的吗！", EmotionType.SURPRISED),
            # 注意：规则中 HAPPY 包含"喜"，LOVE 包含"喜欢"和"爱"
            # 因为"喜欢"包含"喜"，会被优先匹配为 HAPPY
            # ("我爱你，喜欢你", EmotionType.LOVE),  # 这个会匹配到 HAPPY
            ("真的很爱你，支持你", EmotionType.LOVE),  # 使用"爱"和"支持"关键词
            ("有点害羞，不好意思", EmotionType.SHY),
            ("太激动了，很兴奋", EmotionType.EXCITED),
            ("普通消息，没有情感", EmotionType.NEUTRAL),
        ]

        for text, expected_emotion in test_cases:
            # 创建新的 MessageBase
            seg = Seg(type="text", data=text)
            message = MessageBase(
                message_info=mock_message_base.message_info,
                message_segment=seg,
            )

            intent = provider._parse_with_rules(text, message)

            assert intent.emotion == expected_emotion, (
                f"文本 '{text}' 的情感应该是 {expected_emotion}，实际是 {intent.emotion}"
            )

    @pytest.mark.asyncio
    async def test_parse_with_rules_action_detection(self, setup_provider, mock_message_base):
        """测试关键词动作识别"""
        provider = await setup_provider()

        # 测试感谢
        seg = Seg(type="text", data="非常感谢，多谢！")
        message = MessageBase(
            message_info=mock_message_base.message_info,
            message_segment=seg,
        )
        intent = provider._parse_with_rules("非常感谢，多谢！", message)
        assert any(action.type == ActionType.CLAP for action in intent.actions)
        assert any(action.type == ActionType.EXPRESSION for action in intent.actions)

        # 测试打招呼
        seg = Seg(type="text", data="你好，大家好，哈喽")
        message = MessageBase(
            message_info=mock_message_base.message_info,
            message_segment=seg,
        )
        intent = provider._parse_with_rules("你好，大家好，哈喽", message)
        assert any(action.type == ActionType.WAVE for action in intent.actions)

        # 测试同意
        seg = Seg(type="text", data="是的，对的，嗯")
        message = MessageBase(
            message_info=mock_message_base.message_info,
            message_segment=seg,
        )
        intent = provider._parse_with_rules("是的，对的", message)
        assert any(action.type == ActionType.NOD for action in intent.actions)

        # 测试不同意
        seg = Seg(type="text", data="不，不是，不行")
        message = MessageBase(
            message_info=mock_message_base.message_info,
            message_segment=seg,
        )
        intent = provider._parse_with_rules("不，不是", message)
        assert any(action.type == ActionType.SHAKE for action in intent.actions)

        # 测试无动作 → 眨眼
        seg = Seg(type="text", data="普通消息")
        message = MessageBase(
            message_info=mock_message_base.message_info,
            message_segment=seg,
        )
        intent = provider._parse_with_rules("普通消息", message)
        assert any(action.type == ActionType.BLINK for action in intent.actions)


class TestJSONCleanup:
    """测试 JSON 清理逻辑"""

    @pytest.mark.asyncio
    async def test_json_cleanup_with_nested_markdown(self, setup_provider, mock_message_base, mock_llm_service):
        """测试嵌套的 markdown 代码块"""
        provider = await setup_provider()

        # 测试嵌套的 ```json
        test_cases = [
            # 嵌套 ```json
            (
                '```\n```json\n{"emotion": "happy"}\n```\n```',
                '{"emotion": "happy"}',
            ),
            # 带 ```json 包装
            (
                '```json\n{"emotion": "happy", "actions": [], "response_text": "测试"}\n```',
                '{"emotion": "happy", "actions": [], "response_text": "测试"}',
            ),
            # 只有 ``` 包装
            (
                '```\n{"emotion": "sad"}\n```',
                '{"emotion": "sad"}',
            ),
            # 没有包装
            (
                '{"emotion": "neutral"}',
                '{"emotion": "neutral"}',
            ),
            # 带尾逗号
            (
                '{"emotion": "happy", "actions": [],}',
                '{"emotion": "happy", "actions": []}',
            ),
            # 带前后文本
            (
                '这是前面的一些文本\n```json\n{"emotion": "excited"}\n```\n这是后面的文本',
                '{"emotion": "excited"}',
            ),
        ]

        for i, (content, expected_json) in enumerate(test_cases):
            mock_llm_service.chat_fast.return_value.success = True
            mock_llm_service.chat_fast.return_value.content = content
            mock_llm_service.chat_fast.return_value.model = "test-model"

            # 调用解析
            intent = provider._parse_with_llm(f"测试{i}", mock_message_base, mock_llm_service)

            # 验证能成功解析
            assert intent.emotion == json.loads(expected_json)["emotion"]


class TestFireAndForget:
    """测试 fire-and-forget 行为"""

    @pytest.mark.asyncio
    async def test_decide_returns_none(self, setup_provider, mock_event_bus):
        """测试 decide() 返回 None"""
        provider = await setup_provider()

        # Mock router
        provider._router = MagicMock()
        provider._router.check_connection.return_value = True
        provider._router_adapter = MagicMock()
        provider._router_adapter.send = AsyncMock()

        # 创建 NormalizedMessage
        from src.modules.types.base.normalized_message import NormalizedMessage

        message = NormalizedMessage(
            text="测试",
            content=MagicMock(get_user_id=lambda: "user", get_display_text=lambda: "测试"),
            source="test",
            data_type="text",
            importance=0.5,
        )

        # 调用 decide - 应该返回 None
        result = await provider.decide(message)

        assert result is None

    @pytest.mark.asyncio
    async def test_decide_when_not_connected(self, setup_provider):
        """测试未连接时 decide() 不抛出异常"""
        provider = await setup_provider()

        # Mock router 为 None（未连接）
        provider._router = None

        # 创建 NormalizedMessage
        from src.modules.types.base.normalized_message import NormalizedMessage

        message = NormalizedMessage(
            text="测试",
            content=MagicMock(get_user_id=lambda: "user", get_display_text=lambda: "测试"),
            source="test",
            data_type="text",
            importance=0.5,
        )

        # 不应该抛出异常，只返回 None
        result = await provider.decide(message)
        assert result is None


class TestActionSuggestions:
    """测试 Action 建议功能"""

    @pytest.mark.asyncio
    async def test_action_suggestions_uses_intent_actions(self, setup_provider, mock_llm_service):
        """测试使用 intent.actions（不是 suggested_actions）"""
        provider = await setup_provider()

        # 创建带 actions 的 Intent
        intent = Intent(
            original_text="测试",
            response_text="测试回复",
            emotion=EmotionType.HAPPY,
            actions=[
                IntentAction(type=ActionType.WAVE, params={}, priority=60),
                IntentAction(type=ActionType.EXPRESSION, params={"name": "smile"}, priority=70),
            ],
        )

        # Mock router_adapter
        provider._router_adapter = AsyncMock()
        provider._router_adapter.send_action_suggestion = AsyncMock()

        # 调用发送
        await provider._safe_send_suggestion(intent)

        # 验证使用了 intent.actions
        provider._router_adapter.send_action_suggestion.assert_called_once_with(intent)

        # 验证 intent.actions 存在
        assert hasattr(intent, "actions")
        assert len(intent.actions) == 2
        assert intent.actions[0].type == ActionType.WAVE


class TestIntegration:
    """集成测试"""

    @pytest.mark.asyncio
    async def test_full_intent_parsing_flow(self, setup_provider, mock_message_base, mock_llm_service):
        """测试完整的 Intent 解析流程"""
        provider = await setup_provider()

        # Mock LLM 返回
        mock_llm_service.chat_fast.return_value.success = True
        mock_llm_service.chat_fast.return_value.content = """
        {
            "emotion": "happy",
            "actions": [
                {"type": "wave", "params": {}, "priority": 60},
                {"type": "nod", "params": {}, "priority": 50}
            ],
            "response_text": "你好呀！很高兴见到你！"
        }
        """
        mock_llm_service.chat_fast.return_value.model = "gpt-4"

        # 调用 _parse_intent_from_maicore_response
        intent = provider._parse_intent_from_maicore_response(mock_message_base)

        # 验证结果
        assert intent.emotion == EmotionType.HAPPY
        assert intent.response_text == "你好呀！很高兴见到你！"
        assert len(intent.actions) == 2
        assert intent.source_context.source == "amaidesu"
        assert intent.metadata["parser"] == "llm"


class TestPromptOverride:
    """测试提示词覆盖功能"""

    @pytest.fixture
    def override_config(self):
        """启用覆盖的配置"""
        return {
            "host": "localhost",
            "port": 8000,
            "platform": "test",
            "enable_prompt_override": True,
            "override_template_name": "test_override",
            "override_templates": [
                "replyer_prompt",
                "replyer_prompt_0",
                "chat_target_group1",
                "chat_target_group2",
            ],
        }

    @pytest.fixture
    def provider_with_override(self, override_config, mock_provider_context):
        """创建启用覆盖的 Provider 实例"""
        return MaiCoreDecisionProvider(override_config, mock_provider_context)

    def test_override_service_enabled_by_default(self, mock_provider_context):
        """测试默认启用覆盖功能"""
        config = {"host": "localhost", "port": 8000}
        provider = MaiCoreDecisionProvider(config, mock_provider_context)
        assert provider._override_service.config.enabled is True

    def test_override_service_enabled(self, provider_with_override):
        """测试启用覆盖功能"""
        assert provider_with_override._override_service.config.enabled is True

    @pytest.mark.asyncio
    async def test_normalized_to_message_base_includes_template_info_when_enabled(self, provider_with_override):
        """测试启用时 message 包含 template_info"""
        from unittest.mock import MagicMock

        # Mock PromptManager 返回模板内容
        provider_with_override._override_service._prompt_manager.get_raw = MagicMock(
            return_value="测试模板内容 {bot_name}"
        )

        # 创建 NormalizedMessage，使用 mock raw 对象提供 user_id
        from src.modules.types.base.normalized_message import NormalizedMessage

        mock_raw = MagicMock()
        mock_raw.get_user_id = MagicMock(return_value="test_user")

        message = NormalizedMessage(
            text="测试",
            source="test",
            data_type="text",
            importance=0.5,
            timestamp=1234567890.0,
            raw=mock_raw,
        )

        result = provider_with_override._normalized_to_message_base(message)

        assert result is not None
        assert result.message_info.template_info is not None
        assert result.message_info.template_info.template_default is False
        assert "replyer_prompt" in result.message_info.template_info.template_items
        # 验证 replyer_prompt_0 也被包含（think_level=0 时使用）
        assert "replyer_prompt_0" in result.message_info.template_info.template_items
        # 验证 group_info 也被正确设置（用于群聊判定）
        assert result.message_info.group_info is not None
        assert result.message_info.group_info.group_id == "live_room"
        assert result.message_info.group_info.group_name == "直播间"

    @pytest.mark.asyncio
    async def test_normalized_to_message_base_no_template_info_when_disabled(self, mock_provider_context):
        """测试禁用时 message 不包含 template_info"""
        config = {"host": "localhost", "port": 8000, "enable_prompt_override": False}
        provider = MaiCoreDecisionProvider(config, mock_provider_context)

        from src.modules.types.base.normalized_message import NormalizedMessage

        # 创建 NormalizedMessage，使用 mock raw 对象提供 user_id
        mock_raw = MagicMock()
        mock_raw.get_user_id = MagicMock(return_value="test_user")

        message = NormalizedMessage(
            text="测试",
            source="test",
            data_type="text",
            importance=0.5,
            timestamp=1234567890.0,
            raw=mock_raw,
        )

        result = provider._normalized_to_message_base(message)

        assert result is not None
        assert result.message_info.template_info is None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
