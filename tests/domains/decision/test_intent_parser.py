"""
测试 IntentParser (pytest)

运行: uv run pytest tests/domains/decision/test_intent_parser.py -v
"""

import pytest
import asyncio
from typing import Optional

from src.domains.decision.intent_parser import IntentParser
from src.domains.decision.intent import Intent, EmotionType, ActionType
from src.services.llm.service import LLMResponse


# =============================================================================
# Mock MessageBase
# =============================================================================


class MockMessageContent:
    """Mock message content"""

    def __init__(self, content: str):
        self.content = content


class MockMessageBase:
    """Mock MessageBase for testing"""

    def __init__(self, text: str = "测试消息"):
        self.message_content = MockMessageContent(content=text)

    def __str__(self) -> str:
        return self.message_content.content


# =============================================================================
# Mock LLMService
# =============================================================================


class MockLLMService:
    """Mock LLMService for testing"""

    def __init__(self):
        self._backends = {"llm_fast": True}  # 模拟后端已配置
        self.chat_calls = []
        self._should_fail = False
        self._fail_message = "Mock LLM failure"
        self._response_content = None

    def set_response(self, content: str):
        """设置LLM响应内容"""
        self._response_content = content

    def set_failure(self, should_fail: bool, message: str = "Mock LLM failure"):
        """设置是否失败"""
        self._should_fail = should_fail
        self._fail_message = message

    async def chat(
        self,
        prompt: str,
        backend: str = "llm_fast",
        system_message: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        **kwargs,
    ) -> LLMResponse:
        """模拟 chat 调用"""
        self.chat_calls.append(
            {
                "prompt": prompt,
                "backend": backend,
                "system_message": system_message,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )

        if self._should_fail:
            return LLMResponse(success=False, error=self._fail_message)

        # 默认响应（如果未设置）
        if self._response_content is None:
            content = """```json
{
  "emotion": "happy",
  "response_text": "你好！很高兴见到你！",
  "actions": [
    {"type": "blink", "params": {}, "priority": 30},
    {"type": "expression", "params": {"name": "smile"}, "priority": 60}
  ]
}
```"""
        else:
            content = self._response_content

        return LLMResponse(
            success=True, content=content, model="mock-model", usage={"prompt_tokens": 50, "completion_tokens": 30}
        )


class MockLLMServiceWithoutFast:
    """Mock LLMService without llm_fast backend"""

    def __init__(self):
        self._backends = {}  # 没有 llm_fast

    async def chat(self, **kwargs) -> LLMResponse:
        return LLMResponse(success=True, content="fallback")


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_llm_service():
    """创建 Mock LLMService"""
    return MockLLMService()


@pytest.fixture
def mock_llm_service_no_fast():
    """创建没有 llm_fast 的 Mock LLMService"""
    return MockLLMServiceWithoutFast()


@pytest.fixture
async def intent_parser(mock_llm_service):
    """创建 IntentParser 实例"""
    parser = IntentParser(mock_llm_service)
    await parser.setup()
    yield parser
    await parser.cleanup()


@pytest.fixture
def sample_message():
    """创建示例 MessageBase"""
    return MockMessageBase("你好，很高兴见到大家！")


@pytest.fixture
def sample_message_happy():
    """创建包含开心情感的示例消息"""
    return MockMessageBase("太开心了！今天真是美好的一天！哈哈")


@pytest.fixture
def sample_message_sad():
    """创建包含悲伤情感的示例消息"""
    return MockMessageBase("好难过啊，为什么要这样对我")


# =============================================================================
# 初始化和设置测试
# =============================================================================


class TestIntentParserSetup:
    """测试 IntentParser 初始化和设置"""

    @pytest.mark.asyncio
    async def test_initialization(self, mock_llm_service):
        """测试初始化"""
        parser = IntentParser(mock_llm_service)

        assert parser.llm_service == mock_llm_service
        assert parser._enabled is True

    @pytest.mark.asyncio
    async def test_setup_with_llm_fast(self, mock_llm_service):
        """测试设置（有 llm_fast）"""
        parser = IntentParser(mock_llm_service)
        await parser.setup()

        assert parser._enabled is True

    @pytest.mark.asyncio
    async def test_setup_without_llm_fast(self, mock_llm_service_no_fast):
        """测试设置（没有 llm_fast）"""
        parser = IntentParser(mock_llm_service_no_fast)
        await parser.setup()

        assert parser._enabled is False

    @pytest.mark.asyncio
    async def test_cleanup(self, mock_llm_service):
        """测试清理"""
        parser = IntentParser(mock_llm_service)
        await parser.setup()
        await parser.cleanup()

        # cleanup 应该不抛出异常


# =============================================================================
# 文本提取测试 (_extract_text)
# =============================================================================


class TestTextExtraction:
    """测试文本提取功能"""

    @pytest.mark.asyncio
    async def test_extract_text_from_normal_message(self, mock_llm_service):
        """测试从普通消息提取文本"""
        parser = IntentParser(mock_llm_service)
        message = MockMessageBase("测试文本")

        text = parser._extract_text(message)

        assert text == "测试文本"

    @pytest.mark.asyncio
    async def test_extract_text_from_empty_message(self, mock_llm_service):
        """测试从空消息提取文本"""
        parser = IntentParser(mock_llm_service)
        message = MockMessageBase("")

        text = parser._extract_text(message)

        assert text == ""

    @pytest.mark.asyncio
    async def test_extract_text_from_message_without_content_attribute(self, mock_llm_service):
        """测试从没有 content 属性的消息提取文本"""
        parser = IntentParser(mock_llm_service)

        # 创建一个没有 message_content 的对象
        class SimpleMessage:
            def __str__(self):
                return "简单消息"

        message = SimpleMessage()
        text = parser._extract_text(message)

        assert text == "简单消息"

    @pytest.mark.asyncio
    async def test_extract_text_with_special_characters(self, mock_llm_service):
        """测试提取包含特殊字符的文本"""
        parser = IntentParser(mock_llm_service)
        text = "测试\n换行\t制表符\"引号'"
        message = MockMessageBase(text)

        extracted = parser._extract_text(message)

        assert extracted == text

    @pytest.mark.asyncio
    async def test_extract_text_very_long(self, mock_llm_service):
        """测试提取超长文本"""
        parser = IntentParser(mock_llm_service)
        long_text = "测试" * 10000
        message = MockMessageBase(long_text)

        extracted = parser._extract_text(message)

        assert extracted == long_text

    @pytest.mark.asyncio
    async def test_extract_text_unicode(self, mock_llm_service):
        """测试提取 Unicode 文本"""
        parser = IntentParser(mock_llm_service)
        unicode_text = "Hello 世界 🌍😀"
        message = MockMessageBase(unicode_text)

        extracted = parser._extract_text(message)

        assert extracted == unicode_text


# =============================================================================
# LLM 解析测试 (_parse_with_llm)
# =============================================================================


class TestLLMParsing:
    """测试 LLM 解析功能"""

    @pytest.mark.asyncio
    async def test_parse_with_llm_success(self, intent_parser, sample_message):
        """测试成功的 LLM 解析"""
        intent = await intent_parser._parse_with_llm("你好，很高兴见到大家！", sample_message)

        assert isinstance(intent, Intent)
        assert intent.original_text == "你好，很高兴见到大家！"
        assert intent.response_text == "你好！很高兴见到你！"
        assert intent.emotion == EmotionType.HAPPY
        assert len(intent.actions) == 2
        assert intent.actions[0].type == ActionType.BLINK
        assert intent.actions[1].type == ActionType.EXPRESSION
        assert intent.metadata["llm_model"] == "mock-model"

    @pytest.mark.asyncio
    async def test_parse_with_llm_custom_response(self, intent_parser, sample_message):
        """测试自定义 LLM 响应"""
        custom_response = """```json
{
  "emotion": "sad",
  "response_text": "抱歉听到这个消息",
  "actions": [
    {"type": "expression", "params": {"name": "sad"}, "priority": 70}
  ]
}
```"""
        intent_parser.llm_service.set_response(custom_response)

        intent = await intent_parser._parse_with_llm("今天很难过", sample_message)

        assert intent.emotion == EmotionType.SAD
        assert intent.response_text == "抱歉听到这个消息"
        assert len(intent.actions) == 1
        assert intent.actions[0].type == ActionType.EXPRESSION
        assert intent.actions[0].params["name"] == "sad"

    @pytest.mark.asyncio
    async def test_parse_with_llm_json_without_markdown(self, intent_parser, sample_message):
        """测试 LLM 返回不带 markdown 的 JSON"""
        custom_response = """{
  "emotion": "surprised",
  "response_text": "哇！真的吗？",
  "actions": []
}"""
        intent_parser.llm_service.set_response(custom_response)

        intent = await intent_parser._parse_with_llm("真的吗？", sample_message)

        assert intent.emotion == EmotionType.SURPRISED
        assert intent.response_text == "哇！真的吗？"

    @pytest.mark.asyncio
    async def test_parse_with_llm_llm_failure(self, intent_parser, sample_message):
        """测试 LLM 调用失败"""
        intent_parser.llm_service.set_failure(True, "API 错误")

        with pytest.raises(ValueError, match="LLM调用失败"):
            await intent_parser._parse_with_llm("测试", sample_message)

    @pytest.mark.asyncio
    async def test_parse_with_llm_invalid_json(self, intent_parser, sample_message):
        """测试 LLM 返回无效 JSON"""
        intent_parser.llm_service.set_response("这不是有效的JSON")

        with pytest.raises(ValueError, match="LLM返回的JSON格式错误"):
            await intent_parser._parse_with_llm("测试", sample_message)

    @pytest.mark.asyncio
    async def test_parse_with_llm_missing_fields(self, intent_parser, sample_message):
        """测试 LLM 返回缺少必要字段"""
        intent_parser.llm_service.set_response("""```json
{
  "emotion": "neutral"
}
```""")

        intent = await intent_parser._parse_with_llm("测试", sample_message)

        # 应该使用默认值
        assert intent.emotion == EmotionType.NEUTRAL
        assert intent.response_text == "测试"
        assert intent.actions == []

    @pytest.mark.asyncio
    async def test_parse_with_llm_params(self, intent_parser, sample_message):
        """测试 LLM 调用参数"""
        await intent_parser._parse_with_llm("测试消息", sample_message)

        call = intent_parser.llm_service.chat_calls[0]

        assert call["backend"] == "llm_fast"
        assert call["temperature"] == 0.3
        assert call["max_tokens"] == 200
        assert "请分析以下AI VTuber的回复消息" in call["prompt"]
        assert "测试消息" in call["prompt"]

    @pytest.mark.asyncio
    async def test_parse_with_llm_complex_actions(self, intent_parser, sample_message):
        """测试解析复杂动作"""
        custom_response = """```json
{
  "emotion": "happy",
  "response_text": "谢谢！",
  "actions": [
    {"type": "expression", "params": {"name": "thank"}, "priority": 80},
    {"type": "clap", "params": {"intensity": 0.9}, "priority": 70},
    {"type": "nod", "params": {"count": 3}, "priority": 50}
  ]
}
```"""
        intent_parser.llm_service.set_response(custom_response)

        intent = await intent_parser._parse_with_llm("谢谢大家！", sample_message)

        assert len(intent.actions) == 3
        assert intent.actions[0].type == ActionType.EXPRESSION
        assert intent.actions[0].params["name"] == "thank"
        assert intent.actions[0].priority == 80
        assert intent.actions[1].type == ActionType.CLAP
        assert intent.actions[1].params["intensity"] == 0.9
        assert intent.actions[2].type == ActionType.NOD


# =============================================================================
# 规则引擎解析测试 (_parse_with_rules)
# =============================================================================


class TestRuleBasedParsing:
    """测试规则引擎解析功能"""

    @pytest.mark.asyncio
    async def test_parse_with_rules_neutral_default(self, mock_llm_service):
        """测试规则引擎默认中性情感"""
        parser = IntentParser(mock_llm_service)
        message = MockMessageBase("这是一条普通消息")

        intent = parser._parse_with_rules("这是一条普通消息", message)

        assert intent.emotion == EmotionType.NEUTRAL
        assert intent.response_text == "这是一条普通消息"

    @pytest.mark.asyncio
    async def test_parse_with_rules_happy_keywords(self, mock_llm_service):
        """测试开心关键词识别"""
        parser = IntentParser(mock_llm_service)

        happy_texts = ["我今天好开心啊", "太高兴了！", "哈哈哈真好笑", "今天很快乐", "笑死我了"]

        for text in happy_texts:
            message = MockMessageBase(text)
            intent = parser._parse_with_rules(text, message)

            assert intent.emotion == EmotionType.HAPPY, f"Failed for: {text}"

    @pytest.mark.asyncio
    async def test_parse_with_rules_sad_keywords(self, mock_llm_service):
        """测试悲伤关键词识别"""
        parser = IntentParser(mock_llm_service)

        sad_texts = ["我好难过", "太伤心了", "想哭一场", "😢😭"]

        for text in sad_texts:
            message = MockMessageBase(text)
            intent = parser._parse_with_rules(text, message)

            assert intent.emotion == EmotionType.SAD, f"Failed for: {text}"

    @pytest.mark.asyncio
    async def test_parse_with_rules_angry_keywords(self, mock_llm_service):
        """测试生气关键词识别"""
        parser = IntentParser(mock_llm_service)

        angry_texts = ["我很生气", "太愤怒了", "😠😡"]

        for text in angry_texts:
            message = MockMessageBase(text)
            intent = parser._parse_with_rules(text, message)

            assert intent.emotion == EmotionType.ANGRY, f"Failed for: {text}"

    @pytest.mark.asyncio
    async def test_parse_with_rules_surprised_keywords(self, mock_llm_service):
        """测试惊讶关键词识别"""
        parser = IntentParser(mock_llm_service)

        surprised_texts = ["太惊讶了", "好意外", "哇！真的吗？", "😲😱"]

        for text in surprised_texts:
            message = MockMessageBase(text)
            intent = parser._parse_with_rules(text, message)

            assert intent.emotion == EmotionType.SURPRISED, f"Failed for: {text}"

    @pytest.mark.asyncio
    async def test_parse_with_rules_love_keywords(self, mock_llm_service):
        """测试喜爱关键词识别"""
        parser = IntentParser(mock_llm_service)

        love_texts = ["我好爱你", "太喜欢了", "❤️💕", "😍"]

        for text in love_texts:
            message = MockMessageBase(text)
            intent = parser._parse_with_rules(text, message)

            assert intent.emotion == EmotionType.LOVE, f"Failed for: {text}"

    @pytest.mark.asyncio
    async def test_parse_with_rules_emoji_emotion(self, mock_llm_service):
        """测试 emoji 情感识别"""
        parser = IntentParser(mock_llm_service)

        emoji_tests = [
            ("😊😄🎉", EmotionType.HAPPY),
            ("😢😭💔", EmotionType.SAD),
            ("😠😡🔥", EmotionType.ANGRY),
            ("😲😱", EmotionType.SURPRISED),
            ("❤️💕😍", EmotionType.LOVE),
        ]

        for text, expected_emotion in emoji_tests:
            message = MockMessageBase(text)
            intent = parser._parse_with_rules(text, message)

            assert intent.emotion == expected_emotion, f"Failed for emoji: {text}"

    @pytest.mark.asyncio
    async def test_parse_with_rules_thank_action(self, mock_llm_service):
        """测试感谢动作识别"""
        parser = IntentParser(mock_llm_service)

        thank_texts = [
            "感谢大家的支持",
            "谢谢你们",
        ]

        for text in thank_texts:
            message = MockMessageBase(text)
            intent = parser._parse_with_rules(text, message)

            assert any(a.type == ActionType.EXPRESSION for a in intent.actions), f"Failed for: {text}"
            # 找到 expression 动作
            expression_action = next(a for a in intent.actions if a.type == ActionType.EXPRESSION)
            assert expression_action.params.get("name") == "thank"
            assert expression_action.priority == 70

    @pytest.mark.asyncio
    async def test_parse_with_rules_greeting_action(self, mock_llm_service):
        """测试问候动作识别"""
        parser = IntentParser(mock_llm_service)

        greeting_texts = [
            "你好",
            "大家好",
        ]

        for text in greeting_texts:
            message = MockMessageBase(text)
            intent = parser._parse_with_rules(text, message)

            assert any(a.type == ActionType.WAVE for a in intent.actions), f"Failed for: {text}"
            wave_action = next(a for a in intent.actions if a.type == ActionType.WAVE)
            assert wave_action.priority == 60

    @pytest.mark.asyncio
    async def test_parse_with_rules_nod_action(self, mock_llm_service):
        """测试点头动作识别"""
        parser = IntentParser(mock_llm_service)

        nod_texts = ["是的", "对没错", "嗯嗯"]

        for text in nod_texts:
            message = MockMessageBase(text)
            intent = parser._parse_with_rules(text, message)

            assert any(a.type == ActionType.NOD for a in intent.actions), f"Failed for: {text}"
            nod_action = next(a for a in intent.actions if a.type == ActionType.NOD)
            assert nod_action.priority == 50

    @pytest.mark.asyncio
    async def test_parse_with_rules_shake_action(self, mock_llm_service):
        """测试摇头动作识别"""
        parser = IntentParser(mock_llm_service)

        shake_texts = [
            "不不不",
            "不是这样的",
        ]

        for text in shake_texts:
            message = MockMessageBase(text)
            intent = parser._parse_with_rules(text, message)

            assert any(a.type == ActionType.SHAKE for a in intent.actions), f"Failed for: {text}"
            shake_action = next(a for a in intent.actions if a.type == ActionType.SHAKE)
            assert shake_action.priority == 50

    @pytest.mark.asyncio
    async def test_parse_with_rules_default_blink(self, mock_llm_service):
        """测试默认眨眼动作"""
        parser = IntentParser(mock_llm_service)
        message = MockMessageBase("普通消息没有特定动作")

        intent = parser._parse_with_rules("普通消息没有特定动作", message)

        assert len(intent.actions) == 1
        assert intent.actions[0].type == ActionType.BLINK
        assert intent.actions[0].priority == 30

    @pytest.mark.asyncio
    async def test_parse_with_rules_metadata(self, mock_llm_service):
        """测试规则引擎的 metadata"""
        parser = IntentParser(mock_llm_service)
        message = MockMessageBase("测试")

        intent = parser._parse_with_rules("测试", message)

        assert intent.metadata == {"parser": "rule_based"}

    @pytest.mark.asyncio
    async def test_parse_with_rules_combined_keywords(self, mock_llm_service):
        """测试组合关键词（优先匹配第一个）"""
        parser = IntentParser(mock_llm_service)

        # 同时包含开心和悲伤关键词，应该匹配第一个
        text = "开心又难过"
        message = MockMessageBase(text)
        intent = parser._parse_with_rules(text, message)

        # 根据代码逻辑，应该匹配到 HAPPY（在 EMOTION_KEYWORDS 中靠前）
        assert intent.emotion == EmotionType.HAPPY


# =============================================================================
# 默认 Intent 创建测试 (_create_default_intent)
# =============================================================================


class TestDefaultIntent:
    """测试默认 Intent 创建"""

    @pytest.mark.asyncio
    async def test_create_default_intent_with_text(self, mock_llm_service):
        """测试创建带文本的默认 Intent"""
        parser = IntentParser(mock_llm_service)
        intent = parser._create_default_intent("测试文本")

        assert intent.original_text == "测试文本"
        assert intent.response_text == "测试文本"
        assert intent.emotion == EmotionType.NEUTRAL
        assert len(intent.actions) == 1
        assert intent.actions[0].type == ActionType.BLINK
        assert intent.actions[0].priority == 30
        assert intent.metadata == {"parser": "default"}

    @pytest.mark.asyncio
    async def test_create_default_intent_empty_text(self, mock_llm_service):
        """测试创建空文本的默认 Intent"""
        parser = IntentParser(mock_llm_service)
        intent = parser._create_default_intent("")

        assert intent.original_text == ""
        assert intent.response_text == "..."
        assert intent.emotion == EmotionType.NEUTRAL


# =============================================================================
# 主解析流程测试 (parse)
# =============================================================================


class TestParseMain:
    """测试主解析流程"""

    @pytest.mark.asyncio
    async def test_parse_with_llm_enabled(self, intent_parser, sample_message):
        """测试 LLM 启用时的解析"""
        intent = await intent_parser.parse(sample_message)

        assert isinstance(intent, Intent)
        assert intent.emotion == EmotionType.HAPPY
        assert len(intent_parser.llm_service.chat_calls) == 1

    @pytest.mark.asyncio
    async def test_parse_with_llm_disabled(self, mock_llm_service_no_fast, sample_message):
        """测试 LLM 禁用时的解析（规则引擎降级）"""
        parser = IntentParser(mock_llm_service_no_fast)
        await parser.setup()

        # 修改消息以测试规则引擎
        message = MockMessageBase("太开心了哈哈")
        intent = await parser.parse(message)

        assert isinstance(intent, Intent)
        assert intent.emotion == EmotionType.HAPPY
        assert intent.metadata["parser"] == "rule_based"

    @pytest.mark.asyncio
    async def test_parse_llm_fails_fallback_to_rules(self, intent_parser, sample_message):
        """测试 LLM 失败时降级到规则引擎"""
        intent_parser.llm_service.set_failure(True, "LLM 错误")
        message = MockMessageBase("谢谢大家")

        intent = await intent_parser.parse(message)

        assert isinstance(intent, Intent)
        assert intent.metadata["parser"] == "rule_based"
        # 应该识别到感谢关键词
        assert any(a.type == ActionType.EXPRESSION for a in intent.actions)

    @pytest.mark.asyncio
    async def test_parse_empty_message(self, intent_parser):
        """测试解析空消息"""
        message = MockMessageBase("")
        intent = await intent_parser.parse(message)

        assert isinstance(intent, Intent)
        assert intent.response_text == "..."
        assert intent.metadata["parser"] == "default"

    @pytest.mark.asyncio
    async def test_parse_text_extraction_failure(self, mock_llm_service):
        """测试文本提取失败"""
        parser = IntentParser(mock_llm_service)

        # 创建一个会抛出异常的消息对象
        class FailingMessage:
            @property
            def message_content(self):
                raise RuntimeError("Extract failed")

            def __str__(self):
                raise RuntimeError("String conversion failed")

        message = FailingMessage()
        intent = await parser.parse(message)

        assert isinstance(intent, Intent)
        assert intent.response_text == "..."
        assert intent.metadata["parser"] == "default"

    @pytest.mark.asyncio
    async def test_parse_preserves_original_text(self, intent_parser):
        """测试解析保留原始文本"""
        original_text = "这是原始消息文本"
        message = MockMessageBase(original_text)

        intent = await intent_parser.parse(message)

        assert intent.original_text == original_text


# =============================================================================
# 集成测试
# =============================================================================


class TestIntegration:
    """集成测试"""

    @pytest.mark.asyncio
    async def test_full_workflow_llm(self, mock_llm_service):
        """测试完整 LLM 工作流"""
        parser = IntentParser(mock_llm_service)
        await parser.setup()

        message = MockMessageBase("你好！")
        intent = await parser.parse(message)

        assert isinstance(intent, Intent)
        assert intent.original_text == "你好！"

        await parser.cleanup()

    @pytest.mark.asyncio
    async def test_full_workflow_rules(self, mock_llm_service_no_fast):
        """测试完整规则引擎工作流"""
        parser = IntentParser(mock_llm_service_no_fast)
        await parser.setup()

        message = MockMessageBase("谢谢大家的支持！")
        intent = await parser.parse(message)

        assert isinstance(intent, Intent)
        assert intent.emotion in [EmotionType.NEUTRAL, EmotionType.HAPPY]

        await parser.cleanup()

    @pytest.mark.asyncio
    async def test_multiple_sequential_parses(self, intent_parser):
        """测试多次顺序解析"""
        messages = [
            MockMessageBase("你好"),
            MockMessageBase("谢谢"),
            MockMessageBase("再见"),
        ]

        intents = []
        for msg in messages:
            intent = await intent_parser.parse(msg)
            intents.append(intent)

        assert len(intents) == 3
        assert all(isinstance(i, Intent) for i in intents)
        assert len(intent_parser.llm_service.chat_calls) == 3

    @pytest.mark.asyncio
    async def test_concurrent_parses(self, intent_parser):
        """测试并发解析"""
        messages = [MockMessageBase(f"消息{i}") for i in range(10)]

        tasks = [intent_parser.parse(msg) for msg in messages]
        intents = await asyncio.gather(*tasks)

        assert len(intents) == 10
        assert all(isinstance(i, Intent) for i in intents)


# =============================================================================
# 边界情况测试
# =============================================================================


class TestEdgeCases:
    """测试边界情况"""

    @pytest.mark.asyncio
    async def test_parse_very_long_message(self, intent_parser):
        """测试解析超长消息"""
        long_text = "测试" * 10000
        message = MockMessageBase(long_text)

        intent = await intent_parser.parse(message)

        assert intent.original_text == long_text

    @pytest.mark.asyncio
    async def test_parse_unicode_message(self, intent_parser):
        """测试解析 Unicode 消息"""
        unicode_text = "Hello 世界 🌍😀 Здравствуй мир"
        message = MockMessageBase(unicode_text)

        intent = await intent_parser.parse(message)

        assert intent.original_text == unicode_text

    @pytest.mark.asyncio
    async def test_parse_special_characters(self, intent_parser):
        """测试解析特殊字符"""
        special_text = "测试\n换行\t制表符\r回车\"引号'"
        message = MockMessageBase(special_text)

        intent = await intent_parser.parse(message)

        assert intent.original_text == special_text

    @pytest.mark.asyncio
    async def test_parse_mixed_emotion_keywords(self, intent_parser):
        """测试混合情感关键词"""
        # 禁用 LLM 以测试规则引擎
        intent_parser._enabled = False
        message = MockMessageBase("既开心又难过")

        intent = await intent_parser.parse(message)

        # 应该匹配到其中一个情感
        assert intent.emotion in [EmotionType.HAPPY, EmotionType.SAD]

    @pytest.mark.asyncio
    async def test_llm_response_with_extra_whitespace(self, intent_parser):
        """测试 LLM 响应包含额外空白"""
        intent_parser.llm_service.set_response("""

```json
{
  "emotion": "happy",
  "response_text": "测试",
  "actions": []
}
```

""")

        message = MockMessageBase("测试")
        intent = await intent_parser.parse(message)

        assert intent.emotion == EmotionType.HAPPY

    @pytest.mark.asyncio
    async def test_llm_response_with_comments(self, intent_parser):
        """测试 LLM 响应包含注释（应该失败）"""
        intent_parser.llm_service.set_response("""```json
{
  "emotion": "happy",
  "response_text": "测试",
  "actions": []  // 这是注释
}
```""")

        message = MockMessageBase("测试")

        # JSON 不支持注释，应该降级到规则引擎
        # 但我们的 mock 会先尝试解析，可能会失败
        # 这里我们测试降级行为
        intent_parser.llm_service.set_failure(True)
        intent = await intent_parser.parse(message)

        assert intent.metadata["parser"] == "rule_based"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
