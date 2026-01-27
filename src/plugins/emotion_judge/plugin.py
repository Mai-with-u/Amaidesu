"""
Emotion Judge Plugin

情感判断插件，使用LLM判断文本情感并触发热键。
迁移到新的Plugin架构。
"""

from typing import Dict, Any, List

from src.core.event_bus import EventBus
from src.plugins.emotion_judge.emotion_judge_decision_provider import EmotionJudgeDecisionProvider
from src.utils.logger import get_logger


class EmotionJudgePlugin:
    """
    情感判断插件

    使用DecisionProvider分析文本情感并触发热键。
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        # Provider列表
        self._providers: List[EmotionJudgeDecisionProvider] = []

        self.event_bus: EventBus = None

        if not self.config.get("enabled", True):
            self.logger.warning("EmotionJudgePlugin 在配置中被禁用。")
            self.enabled = False
            return

        self.enabled = True

    async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表
        """
        self.event_bus = event_bus
        self.logger.info("设置EmotionJudgePlugin")

        if not self.enabled:
            return []

        # 使用emotion_judge配置节点
        emotion_config = config.get("emotion_judge", {})
        if not emotion_config:
            emotion_config = config  # 向后兼容

        # 创建DecisionProvider
        decision_provider = EmotionJudgeDecisionProvider(emotion_config, event_bus)
        await decision_provider.setup(event_bus, emotion_config)
        self._providers.append(decision_provider)

        self.logger.info(f"EmotionJudgePlugin 设置完成，已创建 {len(self._providers)} 个Provider。")

        return self._providers

    async def cleanup(self):
        """清理资源"""
        self.logger.info("清理EmotionJudgePlugin...")

        # 清理所有Provider
        for provider in self._providers:
            await provider.cleanup()
        self._providers.clear()

        self.logger.info("EmotionJudgePlugin清理完成。")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "EmotionJudge",
            "version": "1.0.0",
            "author": "Amaidesu Team",
            "description": "情感判断插件，使用LLM判断文本情感并触发热键",
            "category": "processing",
            "api_version": "1.0",
        }


# 插件入口点
plugin_entrypoint = EmotionJudgePlugin
