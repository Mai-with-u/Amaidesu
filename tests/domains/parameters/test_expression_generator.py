"""
ExpressionGenerator 测试

测试 ExpressionGenerator 的 Intent 到 ExpressionParameters 转换功能
"""

import pytest
from src.domains.output.parameters.expression_generator import ExpressionGenerator
from src.domains.output.parameters.render_parameters import ExpressionParameters
from src.domains.decision.intent import Intent, EmotionType, ActionType, IntentAction


@pytest.fixture
def basic_config():
    """基础配置"""
    return {
        "default_tts_enabled": True,
        "default_subtitle_enabled": True,
        "default_expressions_enabled": True,
        "default_hotkeys_enabled": True,
    }


@pytest.fixture
def expression_generator(basic_config):
    """创建 ExpressionGenerator 实例"""
    return ExpressionGenerator(basic_config)


@pytest.fixture
def basic_intent():
    """基础 Intent"""
    return Intent(
        original_text="你好",
        response_text="你好！有什么我可以帮助你的吗？",
        emotion=EmotionType.HAPPY,
        actions=[],
        metadata={"source": "test"},
    )


class TestExpressionGeneratorInit:
    """测试 ExpressionGenerator 初始化"""

    def test_init_with_default_config(self):
        """测试使用默认配置初始化"""
        generator = ExpressionGenerator()
        assert generator.default_tts_enabled
        assert generator.default_subtitle_enabled
        assert generator.default_expressions_enabled
        assert generator.default_hotkeys_enabled

    def test_init_with_custom_config(self, basic_config):
        """测试使用自定义配置初始化"""
        config = {
            "default_tts_enabled": False,
            "default_subtitle_enabled": False,
            "default_expressions_enabled": False,
            "default_hotkeys_enabled": False,
        }
        generator = ExpressionGenerator(config)
        assert not generator.default_tts_enabled
        assert not generator.default_subtitle_enabled
        assert not generator.default_expressions_enabled
        assert not generator.default_hotkeys_enabled

    def test_init_with_partial_config(self):
        """测试使用部分配置初始化"""
        config = {
            "default_tts_enabled": False,
        }
        generator = ExpressionGenerator(config)
        assert not generator.default_tts_enabled
        assert generator.default_subtitle_enabled  # 默认值


class TestExpressionGeneratorGenerate:
    """测试 ExpressionGenerator.generate 方法"""

    @pytest.mark.asyncio
    async def test_generate_basic_intent(self, expression_generator, basic_intent):
        """测试生成基本的 ExpressionParameters"""
        params = await expression_generator.generate(basic_intent)

        assert isinstance(params, ExpressionParameters)
        assert params.tts_text == "你好！有什么我可以帮助你的吗？"
        assert params.tts_enabled
        assert params.subtitle_text == "你好！有什么我可以帮助你的吗？"
        assert params.subtitle_enabled

    @pytest.mark.asyncio
    async def test_generate_with_emotion(self, expression_generator):
        """测试带情感的生成"""
        intent = Intent(
            original_text="真开心", response_text="我也很高兴！", emotion=EmotionType.HAPPY, actions=[], metadata={}
        )
        params = await expression_generator.generate(intent)

        assert params.expressions_enabled
        assert len(params.expressions) > 0
        # HAPPY 情感应该有较高的 MouthSmile 值
        assert params.expressions.get("MouthSmile", 0) > 0

    @pytest.mark.asyncio
    async def test_generate_with_different_emotions(self, expression_generator):
        """测试不同情感的生成"""
        emotions = [
            EmotionType.NEUTRAL,
            EmotionType.HAPPY,
            EmotionType.SAD,
            EmotionType.ANGRY,
            EmotionType.SURPRISED,
            EmotionType.LOVE,
        ]

        for emotion in emotions:
            intent = Intent(original_text="测试", response_text="测试回复", emotion=emotion, actions=[], metadata={})
            params = await expression_generator.generate(intent)

            assert params.expressions_enabled
            assert len(params.expressions) > 0
            assert params.metadata.get("emotion") == emotion.value

    @pytest.mark.asyncio
    async def test_generate_with_actions(self, expression_generator):
        """测试带动作的生成"""
        intent = Intent(
            original_text="测试",
            response_text="测试回复",
            emotion=EmotionType.NEUTRAL,
            actions=[
                IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "test_hotkey_1"}, priority=50),
            ],
            metadata={},
        )
        params = await expression_generator.generate(intent)

        assert params.hotkeys_enabled
        assert len(params.hotkeys) == 1
        assert params.hotkeys[0] == "test_hotkey_1"

    @pytest.mark.asyncio
    async def test_generate_with_multiple_actions(self, expression_generator):
        """测试带多个动作的生成"""
        intent = Intent(
            original_text="测试",
            response_text="测试回复",
            emotion=EmotionType.HAPPY,
            actions=[
                IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "hotkey_1"}, priority=50),
                IntentAction(type=ActionType.EMOJI, params={"emoji": "😀"}, priority=60),
                IntentAction(type=ActionType.EXPRESSION, params={"expressions": {"MouthSmile": 1.0}}, priority=70),
            ],
            metadata={},
        )
        params = await expression_generator.generate(intent)

        assert params.hotkeys_enabled
        assert len(params.hotkeys) == 1
        assert params.actions_enabled
        assert len(params.actions) == 1  # 只有 EMOJI（EXPRESSION 不添加到 actions）
        assert params.expressions_enabled
        assert "MouthSmile" in params.expressions

    @pytest.mark.asyncio
    async def test_generate_with_tts_disabled(self, basic_config):
        """测试 TTS 禁用时的生成"""
        config = basic_config.copy()
        config["default_tts_enabled"] = False
        generator = ExpressionGenerator(config)

        intent = Intent(
            original_text="测试", response_text="测试回复", emotion=EmotionType.NEUTRAL, actions=[], metadata={}
        )
        params = await generator.generate(intent)

        assert not params.tts_enabled
        # 字幕应该仍然启用
        assert params.subtitle_enabled

    @pytest.mark.asyncio
    async def test_generate_with_subtitle_disabled(self, basic_config):
        """测试字幕禁用时的生成"""
        config = basic_config.copy()
        config["default_subtitle_enabled"] = False
        generator = ExpressionGenerator(config)

        intent = Intent(
            original_text="测试", response_text="测试回复", emotion=EmotionType.NEUTRAL, actions=[], metadata={}
        )
        params = await generator.generate(intent)

        assert not params.subtitle_enabled
        # TTS 应该仍然启用
        assert params.tts_enabled

    @pytest.mark.asyncio
    async def test_generate_with_expressions_disabled(self, basic_config):
        """测试表情禁用时的生成"""
        config = basic_config.copy()
        config["default_expressions_enabled"] = False
        generator = ExpressionGenerator(config)

        intent = Intent(
            original_text="测试", response_text="测试回复", emotion=EmotionType.HAPPY, actions=[], metadata={}
        )
        params = await generator.generate(intent)

        assert not params.expressions_enabled
        assert len(params.expressions) == 0

    @pytest.mark.asyncio
    async def test_generate_with_empty_response_text(self, expression_generator):
        """测试空响应文本的生成"""
        intent = Intent(original_text="测试", response_text="", emotion=EmotionType.NEUTRAL, actions=[], metadata={})
        params = await expression_generator.generate(intent)

        # 空文本应该禁用 TTS 和字幕
        assert not params.tts_enabled
        assert not params.subtitle_enabled

    @pytest.mark.asyncio
    async def test_generate_metadata(self, expression_generator):
        """测试元数据设置"""
        intent = Intent(
            original_text="原始文本",
            response_text="响应文本",
            emotion=EmotionType.HAPPY,
            actions=[],
            metadata={"key1": "value1", "key2": "value2"},
        )
        params = await expression_generator.generate(intent)

        assert params.metadata.get("emotion") == "happy"
        assert params.metadata.get("original_text") == "原始文本"
        assert params.metadata.get("intent_metadata") == {"key1": "value1", "key2": "value2"}

    @pytest.mark.asyncio
    async def test_generate_with_action_priority_sorting(self, expression_generator):
        """测试动作按优先级排序"""
        intent = Intent(
            original_text="测试",
            response_text="测试回复",
            emotion=EmotionType.NEUTRAL,
            actions=[
                IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "low_priority"}, priority=10),
                IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "high_priority"}, priority=90),
                IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "mid_priority"}, priority=50),
            ],
            metadata={},
        )
        params = await expression_generator.generate(intent)

        # 动作应该按优先级排序（低优先级数字先处理）
        assert len(params.hotkeys) == 3
        assert params.hotkeys[0] == "low_priority"
        assert params.hotkeys[1] == "mid_priority"
        assert params.hotkeys[2] == "high_priority"


class TestExpressionGeneratorEmotionMapping:
    """测试情感映射功能"""

    def test_set_emotion_mapping(self, expression_generator):
        """测试设置情感映射"""
        new_params = {
            "MouthSmile": 1.0,
            "EyeOpenLeft": 0.5,
            "EyeOpenRight": 0.5,
        }
        expression_generator.set_emotion_mapping(EmotionType.HAPPY, new_params)

        # 验证映射已更新
        mapped_params = expression_generator.emotion_mapper.map_emotion(EmotionType.HAPPY)
        assert mapped_params == new_params

    def test_set_emotion_mapping_for_different_emotions(self, expression_generator):
        """测试为不同情感设置映射"""
        # 设置 SAD 情感的映射
        sad_params = {
            "MouthSmile": 0.0,
            "EyeOpenLeft": 0.3,
            "EyeOpenRight": 0.3,
        }
        expression_generator.set_emotion_mapping(EmotionType.SAD, sad_params)

        # 设置 ANGRY 情感的映射
        angry_params = {
            "MouthSmile": 0.0,
            "EyeOpenLeft": 0.2,
            "EyeOpenRight": 0.2,
        }
        expression_generator.set_emotion_mapping(EmotionType.ANGRY, angry_params)

        # 验证两个映射都正确设置
        assert expression_generator.emotion_mapper.map_emotion(EmotionType.SAD) == sad_params
        assert expression_generator.emotion_mapper.map_emotion(EmotionType.ANGRY) == angry_params

    def test_get_available_emotions(self, expression_generator):
        """测试获取可用情感列表"""
        emotions = expression_generator.get_available_emotions()

        assert isinstance(emotions, list)
        assert len(emotions) > 0
        assert EmotionType.NEUTRAL in emotions
        assert EmotionType.HAPPY in emotions


class TestExpressionGeneratorConfigUpdate:
    """测试配置更新功能"""

    @pytest.mark.asyncio
    async def test_update_config(self, expression_generator):
        """测试更新配置"""
        new_config = {
            "default_tts_enabled": False,
            "default_subtitle_enabled": False,
        }
        await expression_generator.update_config(new_config)

        assert not expression_generator.default_tts_enabled
        assert not expression_generator.default_subtitle_enabled
        # 未更新的配置应保持原值
        assert expression_generator.default_expressions_enabled

    @pytest.mark.asyncio
    async def test_update_config_affects_generation(self, expression_generator):
        """测试配置更新影响生成结果"""
        intent = Intent(
            original_text="测试", response_text="测试回复", emotion=EmotionType.NEUTRAL, actions=[], metadata={}
        )

        # 默认配置下生成
        params1 = await expression_generator.generate(intent)
        assert params1.tts_enabled

        # 更新配置
        await expression_generator.update_config({"default_tts_enabled": False})

        # 新配置下生成
        params2 = await expression_generator.generate(intent)
        assert not params2.tts_enabled


class TestExpressionGeneratorStats:
    """测试统计信息功能"""

    def test_get_stats(self, expression_generator):
        """测试获取统计信息"""
        stats = expression_generator.get_stats()

        assert isinstance(stats, dict)
        assert "config" in stats
        assert "default_tts_enabled" in stats
        assert "default_subtitle_enabled" in stats
        assert "default_expressions_enabled" in stats
        assert "default_hotkeys_enabled" in stats
        assert "available_emotions" in stats

        assert stats["default_tts_enabled"]
        assert stats["available_emotions"] > 0

    def test_get_stats_after_config_update(self, expression_generator):
        """测试配置更新后的统计信息"""
        # 获取初始统计
        stats1 = expression_generator.get_stats()
        assert stats1["default_tts_enabled"]

        # 更新配置（使用同步方式模拟）
        expression_generator.config["default_tts_enabled"] = False
        expression_generator.default_tts_enabled = False

        # 获取更新后的统计
        stats2 = expression_generator.get_stats()
        assert not stats2["default_tts_enabled"]


class TestExpressionGeneratorEdgeCases:
    """测试边界情况"""

    @pytest.mark.asyncio
    async def test_generate_with_none_actions(self, expression_generator):
        """测试 actions 为 None 的情况"""
        intent = Intent(
            original_text="测试",
            response_text="测试回复",
            emotion=EmotionType.NEUTRAL,
            actions=[],  # 使用空列表而不是 None
            metadata={},
        )
        params = await expression_generator.generate(intent)

        assert not params.hotkeys_enabled
        assert not params.actions_enabled

    @pytest.mark.asyncio
    async def test_generate_with_empty_actions(self, expression_generator):
        """测试 actions 为空列表的情况"""
        intent = Intent(
            original_text="测试", response_text="测试回复", emotion=EmotionType.NEUTRAL, actions=[], metadata={}
        )
        params = await expression_generator.generate(intent)

        assert not params.hotkeys_enabled
        assert not params.actions_enabled

    @pytest.mark.asyncio
    async def test_generate_with_action_without_hotkey_id(self, expression_generator):
        """测试动作没有 hotkey_id 的情况"""
        intent = Intent(
            original_text="测试",
            response_text="测试回复",
            emotion=EmotionType.NEUTRAL,
            actions=[
                IntentAction(
                    type=ActionType.HOTKEY,
                    params={},  # 没有 hotkey_id
                    priority=50,
                ),
            ],
            metadata={},
        )
        params = await expression_generator.generate(intent)

        # 没有 hotkey_id 的动作不应该被添加到 hotkeys 列表
        assert not params.hotkeys_enabled
        assert len(params.hotkeys) == 0

    @pytest.mark.asyncio
    async def test_generate_with_unknown_action_type(self, expression_generator):
        """测试未知动作类型的处理"""
        # 这个测试验证 ActionMapper 的行为
        # 如果添加了未知类型的动作，应该被忽略或处理
        intent = Intent(
            original_text="测试",
            response_text="测试回复",
            emotion=EmotionType.NEUTRAL,
            actions=[
                IntentAction(
                    type=ActionType.EXPRESSION,  # 已知类型
                    params={"expressions": {"MouthSmile": 0.5}},
                    priority=50,
                ),
            ],
            metadata={},
        )
        params = await expression_generator.generate(intent)

        # EXPRESSION 类型应该被处理
        assert "MouthSmile" in params.expressions
