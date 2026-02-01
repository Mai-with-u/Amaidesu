"""
Expression生成器 - Layer 6 表现生成层核心

职责:
- 将Intent转换为ExpressionParameters
- 协调EmotionMapper和ActionMapper
- 生成完整的渲染参数
"""

from typing import Dict, Any
from src.utils.logger import get_logger

from .render_parameters import ExpressionParameters
from .emotion_mapper import EmotionMapper
from .action_mapper import ActionMapper
from src.layers.intent_analysis.intent import Intent, EmotionType


class ExpressionGenerator:
    """
    表达式生成器

    核心职责:
    - 将Intent转换为ExpressionParameters
    - 协调情感映射和动作映射
    - 生成TTS、字幕、表情、热键等渲染参数

    数据流程:
    Intent → EmotionMapper → VTS表情参数
    Intent → ActionMapper → 动作指令
    → ExpressionParameters → Layer 7
    """

    def __init__(self, config: Dict[str, Any] = None):
        """
        初始化表达式生成器

        Args:
            config: 配置字典
        """
        self.config = config or {}
        self.logger = get_logger("ExpressionGenerator")

        # 初始化映射器
        self.emotion_mapper = EmotionMapper()
        self.action_mapper = ActionMapper()

        # 从配置中读取默认设置
        self.default_tts_enabled = self.config.get("default_tts_enabled", True)
        self.default_subtitle_enabled = self.config.get("default_subtitle_enabled", True)
        self.default_expressions_enabled = self.config.get("default_expressions_enabled", True)
        self.default_hotkeys_enabled = self.config.get("default_hotkeys_enabled", True)

        self.logger.info("ExpressionGenerator初始化完成")

    async def generate(self, intent: Intent) -> ExpressionParameters:
        """
        从Intent生成ExpressionParameters

        Args:
            intent: Intent对象

        Returns:
            ExpressionParameters对象
        """
        self.logger.info(f"开始生成Expression: {intent}")

        # 创建ExpressionParameters
        params = ExpressionParameters()

        # 1. 设置TTS文本
        if self.default_tts_enabled and intent.response_text:
            params.tts_text = intent.response_text
            params.tts_enabled = True
        else:
            params.tts_enabled = False

        # 2. 设置字幕文本
        if self.default_subtitle_enabled and intent.response_text:
            params.subtitle_text = intent.response_text
            params.subtitle_enabled = True
        else:
            params.subtitle_enabled = False

        # 3. 映射情感到抽象表情参数（平台无关）
        if self.default_expressions_enabled:
            emotion_params = self.emotion_mapper.map_emotion(intent.emotion)
            params.expressions = emotion_params
            params.expressions_enabled = True
        else:
            params.expressions_enabled = False

        # 4. 映射动作到渲染指令
        if intent.actions:
            action_result = self.action_mapper.map_actions(intent.actions)
            params.hotkeys = action_result.get("hotkeys", [])
            params.hotkeys_enabled = self.default_hotkeys_enabled and len(params.hotkeys) > 0
            params.actions = action_result.get("actions", [])
            params.actions_enabled = len(params.actions) > 0

            # 如果动作中有表情参数，合并到expressions
            action_expressions = action_result.get("expressions", {})
            if action_expressions:
                params.expressions.update(action_expressions)
        else:
            params.hotkeys_enabled = False
            params.actions_enabled = False

        # 5. 设置元数据
        params.metadata.update(
            {
                "emotion": intent.emotion.value,
                "original_text": intent.original_text,
                "intent_metadata": intent.metadata,
            }
        )

        self.logger.info(f"Expression生成完成: {params}")
        return params

    def set_emotion_mapping(self, emotion: EmotionType, params: Dict[str, float]):
        """
        设置情感映射（运行时动态调整）

        Args:
            emotion: 情感类型
            params: 抽象表情参数字典（平台无关）
        """
        self.emotion_mapper.set_emotion_mapping(emotion, params)
        self.logger.info(f"情感映射已更新: {emotion}")

    def get_available_emotions(self) -> list:
        """
        获取所有可用的情感类型

        Returns:
            情感类型列表
        """
        return self.emotion_mapper.get_available_emotions()

    async def update_config(self, config: Dict[str, Any]):
        """
        更新配置

        Args:
            config: 新的配置字典
        """
        self.config.update(config)

        # 更新默认设置
        self.default_tts_enabled = self.config.get("default_tts_enabled", True)
        self.default_subtitle_enabled = self.config.get("default_subtitle_enabled", True)
        self.default_expressions_enabled = self.config.get("default_expressions_enabled", True)
        self.default_hotkeys_enabled = self.config.get("default_hotkeys_enabled", True)

        self.logger.info("ExpressionGenerator配置已更新")

    def get_stats(self) -> Dict[str, Any]:
        """
        获取生成器统计信息

        Returns:
            统计信息字典
        """
        return {
            "config": self.config,
            "default_tts_enabled": self.default_tts_enabled,
            "default_subtitle_enabled": self.default_subtitle_enabled,
            "default_expressions_enabled": self.default_expressions_enabled,
            "default_hotkeys_enabled": self.default_hotkeys_enabled,
            "available_emotions": len(self.get_available_emotions()),
        }
