"""
LLMPDecisionProvider 测试

测试 LLM 决策提供者的核心功能：
- 结构化 Intent 生成（含 emotion 和 actions）
- JSON 清理逻辑（处理 markdown、尾逗号等）
- 降级机制（simple/echo/error 模式）
- 动作类型映射
- 默认动作添加逻辑

运行: uv run pytest tests/domains/decision/providers/test_llm_decision_provider.py -v
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domains.decision.providers.llm import LLMPDecisionProvider
from src.modules.context import MessageRole
from src.modules.events.names import CoreEvents
from src.modules.llm.manager import LLMResponse
from src.modules.types import ActionType
from src.modules.types.base.normalized_message import NormalizedMessage


# =============================================================================
# Mock Content 类（用于 NormalizedMessage）
# =============================================================================


class MockContent:
    """模拟的 StructuredContent"""

    def get_display_text(self) -> str:
        return "测试消息"

    def get_user_id(self) -> str:
        return "user123"

    type = "text"


# =============================================================================
# Fixture
# =============================================================================


@pytest.fixture
def llm_config():
    """LLM Decision Provider 配置"""
    return {
        "type": "llm",
        "client": "llm",
        "fallback_mode": "simple",
    }


@pytest.fixture
def llm_config_echo():
    """使用 echo 降级模式的配置"""
    return {
        "type": "llm",
        "client": "llm",
        "fallback_mode": "echo",
    }


@pytest.fixture
def llm_config_error():
    """使用 error 降级模式的配置"""
    return {
        "type": "llm",
        "client": "llm",
        "fallback_mode": "error",
    }


@pytest.fixture
def mock_llm_service():
    """Mock LLM Service"""
    mock_service = AsyncMock()
    return mock_service


@pytest.fixture
def mock_context_service():
    """Mock ContextService"""
    mock_service = AsyncMock()
    # 模拟 add_message 和 get_history 方法
    mock_service.add_message = AsyncMock()
    mock_service.get_history = AsyncMock(return_value=[])
    return mock_service


@pytest.fixture
def mock_event_bus():
    """Mock EventBus"""
    mock = MagicMock()
    # emit 需要是 AsyncMock 因为它是异步方法
    mock.emit = AsyncMock()
    return mock


@pytest.fixture
def mock_config_service():
    """Mock ConfigService"""
    mock_service = MagicMock()
    # 模拟 get_section 方法
    mock_service.get_section = MagicMock(
        return_value={
            "bot_name": "爱德丝",
            "personality": "活泼开朗，有些调皮",
            "style_constraints": "口语化，使用网络流行语",
        }
    )
    return mock_service


@pytest.fixture
def normalized_message():
    """创建标准化的 NormalizedMessage"""
    return NormalizedMessage(
        text="你好",
        content=MockContent(),
        source="test",
        data_type="text",
        importance=0.5,
    )


@pytest.fixture
async def setup_provider(llm_config, mock_llm_service, mock_context_service, mock_event_bus, mock_config_service):
    """设置并返回已初始化的 Provider"""

    async def _setup(config=llm_config):
        provider = LLMPDecisionProvider(config)
        await provider.start(
            event_bus=mock_event_bus,
            config={},
            dependencies={
                "llm_service": mock_llm_service,
                "context_service": mock_context_service,
                "config_service": mock_config_service,
            },
        )
        return provider

    return _setup


# =============================================================================
# 初始化和配置测试
# =============================================================================


class TestLLMPDecisionProviderInit:
    """测试 Provider 初始化"""

    def test_init_with_config(self, llm_config):
        """测试使用配置初始化"""
        provider = LLMPDecisionProvider(llm_config)
        assert provider.typed_config.type == "llm"
        assert provider.typed_config.client == "llm"
        assert provider.typed_config.fallback_mode == "simple"

    def test_init_with_different_client(self):
        """测试使用不同的客户端类型"""
        config = {"type": "llm", "client": "llm_fast", "fallback_mode": "echo"}
        provider = LLMPDecisionProvider(config)
        assert provider.typed_config.client == "llm_fast"
        assert provider.typed_config.fallback_mode == "echo"


# =============================================================================
# 结构化 Intent 生成测试
# =============================================================================


class TestStructuredIntentGeneration:
    """测试结构化 Intent 生成"""

    @pytest.mark.asyncio
    async def test_structured_intent_generation_success(self, setup_provider, mock_llm_service, normalized_message):
        """测试：Mock LLM 返回有效 JSON（含 text, emotion, actions）"""
        provider = await setup_provider()

        # Mock LLM 返回结构化 JSON
        mock_llm_service.chat.return_value = LLMResponse(
            success=True,
            content="""{
                "text": "你好呀！很高兴见到你！",
                "emotion": "happy",
                "actions": [
                    {"type": "wave", "params": {"duration": 2}, "priority": 80},
                    {"type": "expression", "params": {"name": "smile"}, "priority": 70}
                ]
            }""",
        )

        result = await provider.decide(normalized_message)

        # 验证 decide() 返回 None
        assert result is None

        # 验证 event_bus 发布 decision.intent 事件
        provider.event_bus.emit.assert_called_once()
        call_args = provider.event_bus.emit.call_args
        assert call_args[0][0] == CoreEvents.DECISION_INTENT
        # 获取发布的 IntentPayload
        intent_payload = call_args[0][1]
        # IntentPayload 通过 intent_data 存储 Intent 数据（序列化为字典）
        assert intent_payload.response_text == "你好呀！很高兴见到你！"
        assert intent_payload.emotion == "happy"
        assert len(intent_payload.actions) == 2
        # actions 是字典列表（因为经过 JSON 序列化）
        assert intent_payload.actions[0]["type"] == ActionType.WAVE
        assert intent_payload.actions[0]["params"] == {"duration": 2}
        assert intent_payload.actions[0]["priority"] == 80
        assert intent_payload.actions[1]["type"] == ActionType.EXPRESSION
        assert intent_payload.metadata.get("parser") == "llm_structured"

        # 验证上下文保存
        assert mock_llm_service.chat.called
        assert provider._context_service.add_message.called

    @pytest.mark.asyncio
    async def test_structured_intent_with_single_action(self, setup_provider, mock_llm_service, normalized_message):
        """测试：单个动作的 Intent"""
        provider = await setup_provider()

        mock_llm_service.chat.return_value = LLMResponse(
            success=True,
            content="""{"text": "嗯嗯", "emotion": "neutral", "actions": [{"type": "nod", "params": {}}]}""",
        )

        result = await provider.decide(normalized_message)

        # 验证 decide() 返回 None
        assert result is None

        # 验证 event_bus 发布事件
        provider.event_bus.emit.assert_called_once()
        call_args = provider.event_bus.emit.call_args
        intent_payload = call_args[0][1]
        assert intent_payload.response_text == "嗯嗯"
        assert intent_payload.emotion == "neutral"
        assert len(intent_payload.actions) == 1
        assert intent_payload.actions[0]["type"] == ActionType.NOD


# =============================================================================
# JSON 清理逻辑测试
# =============================================================================


class TestJSONCleanup:
    """测试 JSON 清理逻辑"""

    def test_json_cleanup_handles_markdown(self, llm_config):
        """测试：Mock LLM 返回带 ```json 包装的 JSON"""
        provider = LLMPDecisionProvider(llm_config)
        raw_output = """```json
        {
            "text": "测试回复",
            "emotion": "happy",
            "actions": []
        }
        ```"""
        cleaned = provider._clean_llm_json(raw_output)
        assert '"text": "测试回复"' in cleaned
        assert "```json" not in cleaned
        assert "```" not in cleaned

    def test_json_cleanup_with_trailing_comma(self, llm_config):
        """测试：Mock LLM 返回带尾逗号的 JSON"""
        provider = LLMPDecisionProvider(llm_config)

        # 测试对象中的尾逗号
        raw_with_object_comma = '{"text": "测试", "emotion": "happy",}'
        cleaned = provider._clean_llm_json(raw_with_object_comma)
        assert cleaned == '{"text": "测试", "emotion": "happy"}'

        # 测试数组中的尾逗号
        raw_with_array_comma = '{"actions": ["wave", "nod",]}'
        cleaned = provider._clean_llm_json(raw_with_array_comma)
        assert cleaned == '{"actions": ["wave", "nod"]}'

    def test_json_cleanup_extracts_json_braces(self, llm_config):
        """测试：从混合文本中提取 JSON 部分"""
        provider = LLMPDecisionProvider(llm_config)
        raw_output = '这是一些解释文字\n{"text": "回复", "emotion": "neutral"}\n更多解释'
        cleaned = provider._clean_llm_json(raw_output)
        assert cleaned == '{"text": "回复", "emotion": "neutral"}'

    def test_json_cleanup_preserves_valid_json(self, llm_config):
        """测试：对有效 JSON 不做改动"""
        provider = LLMPDecisionProvider(llm_config)
        valid_json = '{"text": "测试", "emotion": "happy", "actions": []}'
        cleaned = provider._clean_llm_json(valid_json)
        assert cleaned == valid_json


# =============================================================================
# 降级机制测试
# =============================================================================


class TestFallbackMechanism:
    """测试降级机制"""

    @pytest.mark.asyncio
    async def test_fallback_on_json_parse_error(self, setup_provider, mock_llm_service, normalized_message):
        """测试：Mock LLM 返回无效 JSON"""
        provider = await setup_provider()

        # Mock 返回无效 JSON
        mock_llm_service.chat.return_value = LLMResponse(
            success=True,
            content="这不是有效的 JSON {invalid",
        )

        result = await provider.decide(normalized_message)

        # 验证 decide() 返回 None
        assert result is None

        # 验证调用降级逻辑并通过 event_bus 发布
        provider.event_bus.emit.assert_called_once()
        call_args = provider.event_bus.emit.call_args
        intent_payload = call_args[0][1]
        assert intent_payload.response_text == normalized_message.text
        assert intent_payload.emotion == "neutral"
        assert intent_payload.metadata.get("parser") == "llm_fallback"

    @pytest.mark.asyncio
    async def test_fallback_on_llm_failure(self, setup_provider, mock_llm_service, normalized_message):
        """测试：LLM 调用失败时的降级"""
        provider = await setup_provider()

        # Mock LLM 调用失败
        mock_llm_service.chat.return_value = LLMResponse(
            success=False,
            content=None,
            error="API 错误",
        )

        result = await provider.decide(normalized_message)

        # 验证 decide() 返回 None
        assert result is None

        # 验证降级响应通过 event_bus 发布
        provider.event_bus.emit.assert_called_once()
        call_args = provider.event_bus.emit.call_args
        intent_payload = call_args[0][1]
        assert intent_payload.response_text == normalized_message.text
        assert intent_payload.metadata.get("parser") == "llm_fallback"


# =============================================================================
# Fallback Mode 测试
# =============================================================================


class TestFallbackModes:
    """测试各种 Fallback Mode"""

    @pytest.mark.asyncio
    async def test_fallback_mode_simple(
        self,
        llm_config,
        mock_llm_service,
        mock_context_service,
        mock_event_bus,
        mock_config_service,
        normalized_message,
    ):
        """测试：fallback_mode="simple" 返回原始输入文本"""
        provider = LLMPDecisionProvider(llm_config)
        await provider.start(
            event_bus=mock_event_bus,
            config={},
            dependencies={
                "llm_service": mock_llm_service,
                "context_service": mock_context_service,
                "config_service": mock_config_service,
            },
        )

        # Mock LLM 失败
        mock_llm_service.chat.return_value = LLMResponse(
            success=False,
            content=None,
            error="API 错误",
        )

        result = await provider.decide(normalized_message)

        # 验证 decide() 返回 None
        assert result is None

        # 验证通过 event_bus 发布
        provider.event_bus.emit.assert_called_once()
        call_args = provider.event_bus.emit.call_args
        intent_payload = call_args[0][1]
        assert intent_payload.response_text == normalized_message.text
        assert intent_payload.emotion == "neutral"

    @pytest.mark.asyncio
    async def test_fallback_mode_echo(
        self,
        llm_config_echo,
        mock_llm_service,
        mock_context_service,
        mock_event_bus,
        mock_config_service,
        normalized_message,
    ):
        """测试：fallback_mode="echo" 复读用户输入"""
        provider = LLMPDecisionProvider(llm_config_echo)
        await provider.start(
            event_bus=mock_event_bus,
            config={},
            dependencies={
                "llm_service": mock_llm_service,
                "context_service": mock_context_service,
                "config_service": mock_config_service,
            },
        )

        # Mock LLM 失败
        mock_llm_service.chat.return_value = LLMResponse(
            success=False,
            content=None,
            error="API 错误",
        )

        result = await provider.decide(normalized_message)

        # 验证 decide() 返回 None
        assert result is None

        # 验证通过 event_bus 发布
        provider.event_bus.emit.assert_called_once()
        call_args = provider.event_bus.emit.call_args
        intent_payload = call_args[0][1]
        assert intent_payload.response_text == f"你说：{normalized_message.text}"
        assert intent_payload.emotion == "neutral"

    @pytest.mark.asyncio
    async def test_fallback_mode_error(
        self,
        llm_config_error,
        mock_llm_service,
        mock_context_service,
        mock_event_bus,
        mock_config_service,
        normalized_message,
    ):
        """测试：fallback_mode="error" 抛出异常"""
        provider = LLMPDecisionProvider(llm_config_error)
        await provider.start(
            event_bus=mock_event_bus,
            config={},
            dependencies={
                "llm_service": mock_llm_service,
                "context_service": mock_context_service,
                "config_service": mock_config_service,
            },
        )

        # Mock LLM 失败
        mock_llm_service.chat.return_value = LLMResponse(
            success=False,
            content=None,
            error="API 错误",
        )

        with pytest.raises(RuntimeError) as exc_info:
            await provider.decide(normalized_message)

        assert "LLM 请求失败" in str(exc_info.value)


# =============================================================================
# 动作类型映射测试
# =============================================================================


class TestActionTypeMapping:
    """测试动作类型映射"""

    def test_action_type_mapping(self, llm_config):
        """测试：_map_action_type 方法"""
        provider = LLMPDecisionProvider(llm_config)

        # 测试所有标准类型
        assert provider._map_action_type("expression") == ActionType.EXPRESSION
        assert provider._map_action_type("hotkey") == ActionType.HOTKEY
        assert provider._map_action_type("emoji") == ActionType.EMOJI
        assert provider._map_action_type("blink") == ActionType.BLINK
        assert provider._map_action_type("nod") == ActionType.NOD
        assert provider._map_action_type("shake") == ActionType.SHAKE
        assert provider._map_action_type("wave") == ActionType.WAVE
        assert provider._map_action_type("clap") == ActionType.CLAP
        assert provider._map_action_type("sticker") == ActionType.STICKER
        assert provider._map_action_type("motion") == ActionType.MOTION
        assert provider._map_action_type("custom") == ActionType.CUSTOM
        assert provider._map_action_type("game_action") == ActionType.GAME_ACTION
        assert provider._map_action_type("none") == ActionType.NONE

    def test_action_type_mapping_aliases(self, llm_config):
        """测试：别名映射"""
        provider = LLMPDecisionProvider(llm_config)
        assert provider._map_action_type("speak") == ActionType.EXPRESSION
        assert provider._map_action_type("gesture") == ActionType.EXPRESSION

    def test_action_type_mapping_unknown(self, llm_config):
        """测试：未知类型映射到 NONE"""
        provider = LLMPDecisionProvider(llm_config)
        assert provider._map_action_type("unknown_type") == ActionType.NONE
        assert provider._map_action_type("") == ActionType.NONE


# =============================================================================
# 默认动作测试
# =============================================================================


class TestDefaultAction:
    """测试默认动作逻辑"""

    @pytest.mark.asyncio
    async def test_default_action_when_no_actions(self, setup_provider, mock_llm_service, normalized_message):
        """测试：Mock LLM 返回空 actions 数组"""
        provider = await setup_provider()

        # Mock 返回空 actions
        mock_llm_service.chat.return_value = LLMResponse(
            success=True,
            content="""{"text": "回复文本", "emotion": "neutral", "actions": []}""",
        )

        result = await provider.decide(normalized_message)

        # 验证 decide() 返回 None
        assert result is None

        # 验证通过 event_bus 发布
        provider.event_bus.emit.assert_called_once()
        call_args = provider.event_bus.emit.call_args
        intent_payload = call_args[0][1]

        # 验证自动添加默认眨眼动作（actions 是字典列表）
        assert len(intent_payload.actions) == 1
        assert intent_payload.actions[0]["type"] == ActionType.BLINK
        assert intent_payload.actions[0]["params"] == {}
        assert intent_payload.actions[0]["priority"] == 30

    @pytest.mark.asyncio
    async def test_no_default_action_when_actions_exist(self, setup_provider, mock_llm_service, normalized_message):
        """测试：当 actions 存在时不添加默认动作"""
        provider = await setup_provider()

        # Mock 返回有 actions
        mock_llm_service.chat.return_value = LLMResponse(
            success=True,
            content="""{"text": "回复", "emotion": "happy", "actions": [{"type": "wave"}]}""",
        )

        result = await provider.decide(normalized_message)

        # 验证 decide() 返回 None
        assert result is None

        # 验证通过 event_bus 发布
        provider.event_bus.emit.assert_called_once()
        call_args = provider.event_bus.emit.call_args
        intent_payload = call_args[0][1]

        # 验证只有 wave 动作，没有默认的 blink
        assert len(intent_payload.actions) == 1
        assert intent_payload.actions[0]["type"] == ActionType.WAVE


# =============================================================================
# 上下文管理测试
# =============================================================================


class TestContextManagement:
    """测试上下文管理"""

    @pytest.mark.asyncio
    async def test_context_service_add_messages(
        self, setup_provider, mock_llm_service, mock_context_service, normalized_message
    ):
        """测试：保存用户消息和助手回复到上下文"""
        provider = await setup_provider()

        mock_llm_service.chat.return_value = LLMResponse(
            success=True,
            content="""{"text": "回复内容", "emotion": "happy", "actions": []}""",
        )

        result = await provider.decide(normalized_message)

        # 验证 decide() 返回 None
        assert result is None

        # 验证上下文服务被调用
        # 应该调用两次：保存用户消息和保存助手回复
        assert mock_context_service.add_message.call_count == 2

        # 验证第一次调用（用户消息）
        first_call = mock_context_service.add_message.call_args_list[0]
        assert first_call[1]["role"] == MessageRole.USER
        assert first_call[1]["content"] == normalized_message.text

        # 验证第二次调用（助手回复）
        second_call = mock_context_service.add_message.call_args_list[1]
        assert second_call[1]["role"] == MessageRole.ASSISTANT
        assert second_call[1]["content"] == "回复内容"

    @pytest.mark.asyncio
    async def test_context_service_get_history(
        self, setup_provider, mock_llm_service, mock_context_service, normalized_message
    ):
        """测试：获取历史上下文"""
        provider = await setup_provider()

        # Mock 返回历史消息
        mock_context_service.get_history.return_value = [
            MagicMock(role=MessageRole.USER, content="历史消息1"),
            MagicMock(role=MessageRole.ASSISTANT, content="历史回复1"),
        ]

        mock_llm_service.chat.return_value = LLMResponse(
            success=True,
            content="""{"text": "回复", "emotion": "neutral", "actions": []}""",
        )

        result = await provider.decide(normalized_message)

        # 验证 decide() 返回 None
        assert result is None

        # 验证调用了 get_history
        mock_context_service.get_history.assert_called_once()


# =============================================================================
# Emotion 解析测试
# =============================================================================


class TestEmotionParsing:
    """测试 Emotion 解析"""

    @pytest.mark.asyncio
    async def test_emotion_parsing_valid(self, setup_provider, mock_llm_service, normalized_message):
        """测试：有效的 emotion 类型"""
        provider = await setup_provider()

        for emotion in ["neutral", "happy", "sad", "angry", "surprised"]:
            # 重置 mock 以便每次迭代都调用
            provider.event_bus.reset_mock()
            mock_llm_service.chat.return_value = LLMResponse(
                success=True,
                content=f'{{"text": "回复", "emotion": "{emotion}", "actions": []}}',
            )

            result = await provider.decide(normalized_message)
            assert result is None

            # 验证 event_bus 发布事件
            call_args = provider.event_bus.emit.call_args
            intent_payload = call_args[0][1]
            assert intent_payload.emotion == emotion

    @pytest.mark.asyncio
    async def test_emotion_parsing_invalid_fallback(self, setup_provider, mock_llm_service, normalized_message):
        """测试：无效的 emotion 类型回退到 NEUTRAL"""
        provider = await setup_provider()

        mock_llm_service.chat.return_value = LLMResponse(
            success=True,
            content="""{"text": "回复", "emotion": "invalid_emotion", "actions": []}""",
        )

        result = await provider.decide(normalized_message)

        # 验证 decide() 返回 None
        assert result is None

        # 验证通过 event_bus 发布
        call_args = provider.event_bus.emit.call_args
        intent_payload = call_args[0][1]
        assert intent_payload.emotion == "neutral"


# =============================================================================
# 统计信息测试
# =============================================================================


class TestStatistics:
    """测试统计信息"""

    @pytest.mark.asyncio
    async def test_statistics_success_count(self, setup_provider, mock_llm_service, normalized_message):
        """测试：成功请求计数"""
        provider = await setup_provider()

        mock_llm_service.chat.return_value = LLMResponse(
            success=True,
            content="""{"text": "回复", "emotion": "neutral", "actions": []}""",
        )

        result1 = await provider.decide(normalized_message)
        assert result1 is None

        # 需要重置 event_bus 否则第二次调用会有问题
        provider.event_bus.reset_mock()

        result2 = await provider.decide(normalized_message)
        assert result2 is None

        stats = provider.get_statistics()
        assert stats["total_requests"] == 2
        assert stats["successful_requests"] == 2
        assert stats["failed_requests"] == 0

    @pytest.mark.asyncio
    async def test_statistics_failure_count(self, setup_provider, mock_llm_service, normalized_message):
        """测试：失败请求计数"""
        provider = await setup_provider()

        # Mock 失败
        mock_llm_service.chat.return_value = LLMResponse(
            success=False,
            content=None,
            error="API 错误",
        )

        result = await provider.decide(normalized_message)
        assert result is None

        stats = provider.get_statistics()
        assert stats["total_requests"] == 1
        assert stats["successful_requests"] == 0
        assert stats["failed_requests"] == 1


# =============================================================================
# 生命周期测试
# =============================================================================


class TestLifecycle:
    """测试生命周期"""

    @pytest.mark.asyncio
    async def test_cleanup_outputs_statistics(self, setup_provider, mock_llm_service, normalized_message):
        """测试：cleanup 输出统计信息"""
        provider = await setup_provider()

        mock_llm_service.chat.return_value = LLMResponse(
            success=True,
            content="""{"text": "回复", "emotion": "neutral", "actions": []}""",
        )

        result = await provider.decide(normalized_message)
        assert result is None

        await provider.cleanup()

        # cleanup 不应该抛出异常


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
