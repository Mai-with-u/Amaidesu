"""
Emotion Judge Decision Provider

情感判断决策Provider，使用LLM判断文本情感并触发热键。
"""

import time
from typing import Optional, TYPE_CHECKING

from openai import AsyncOpenAI

from src.core.base.decision_provider import DecisionProvider
from src.domains.decision.intent import Intent, EmotionType, ActionType, IntentAction
from src.core.utils.logger import get_logger

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from src.core.base.normalized_message import NormalizedMessage


class EmotionJudgeDecisionProvider(DecisionProvider):
    """
    情感判断决策Provider

    使用LLM分析文本情感，生成包含动作的Intent。

    配置示例:
        ```toml
        [providers.decision.overrides]
        emotion_judge.base_url = "https://api.siliconflow.cn/v1/"
        emotion_judge.api_key = "your-api-key"
        emotion_judge.cool_down_seconds = 10
        ```
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger("EmotionJudgeDecisionProvider")

        # 配置
        self.base_url = self.config.get("base_url", "https://api.siliconflow.cn/v1/")
        self.api_key = self.config.get("api_key", "")
        self.model_config = self.config.get("model", {})

        # 冷却时间
        self.cool_down_seconds = self.config.get("cool_down_seconds", 10)
        self.last_trigger_time: float = 0.0

        # 初始化OpenAI客户端
        self.client = None
        if self.api_key:
            self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
            self.logger.info("EmotionJudgeDecisionProvider 初始化成功")
        else:
            self.logger.warning("EmotionJudgeDecisionProvider 缺少 API Key，功能将禁用")

    async def setup(self, event_bus: "EventBus", config: Optional[dict] = None, dependencies: Optional[dict] = None) -> None:
        """设置Provider"""
        super().setup(event_bus, config, dependencies)
        self.logger.info("EmotionJudgeDecisionProvider 设置完成")

    async def decide(self, normalized_message: "NormalizedMessage") -> Intent:
        """
        决策 - 判断情感并生成Intent

        Args:
            normalized_message: 标准化消息

        Returns:
            Intent: 包含情感和动作的决策意图
        """
        text = normalized_message.text
        if not text:
            return self._create_default_intent(normalized_message)

        # 检查冷却时间
        current_time = time.monotonic()
        if current_time - self.last_trigger_time < self.cool_down_seconds:
            remaining_cooldown = self.cool_down_seconds - (current_time - self.last_trigger_time)
            self.logger.debug(f"情感判断冷却中，使用默认Intent。剩余 {remaining_cooldown:.1f} 秒")
            return self._create_default_intent(normalized_message)

        # 执行情感判断
        emotion = await self._judge_emotion(text)

        # 更新上次触发时间
        self.last_trigger_time = current_time

        # 根据情感生成动作
        actions = self._create_actions_for_emotion(emotion)

        return Intent(
            original_text=text,
            response_text=text,  # 情感判断不改变原文
            emotion=emotion,
            actions=actions,
            metadata={"parser": "emotion_judge", "judged_emotion": emotion.value},
        )

    def _create_default_intent(self, normalized_message: "NormalizedMessage") -> Intent:
        """创建默认Intent"""
        return Intent(
            original_text=normalized_message.text,
            response_text=normalized_message.text,
            emotion=EmotionType.NEUTRAL,
            actions=[IntentAction(type=ActionType.BLINK, params={}, priority=30)],
            metadata={"parser": "emotion_judge", "fallback": True},
        )

    async def _judge_emotion(self, text: str) -> EmotionType:
        """
        使用 LLM 判断文本的情感

        Args:
            text: 文本内容

        Returns:
            情感类型
        """
        if not self.client:
            self.logger.warning("EmotionJudgeDecisionProvider 缺少 API Key，使用规则判断")
            return self._judge_emotion_by_rules(text)

        try:
            response = await self.client.chat.completions.create(
                model=self.model_config.get("name", "Qwen/Qwen2.5-7B-Instruct"),
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一个情感分析助手。分析文本的情感，只返回以下情感之一："
                            "neutral（中性）、happy（开心）、sad（难过）、angry（生气）、"
                            "surprised（惊讶）、love（喜爱）。只输出情感单词，不要包含其他内容。"
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                max_tokens=self.model_config.get("max_tokens", 10),
                temperature=self.model_config.get("temperature", 0.3),
            )

            if response.choices and response.choices[0].message:
                emotion_str = response.choices[0].message.content.strip().lower()
                # 简单的后处理
                emotion_str = emotion_str.strip("'\"")
                self.logger.info(f"文本 '{text[:30]}...' 的情感判断结果: {emotion_str}")

                # 映射到EmotionType
                emotion_map = {
                    "neutral": EmotionType.NEUTRAL,
                    "happy": EmotionType.HAPPY,
                    "sad": EmotionType.SAD,
                    "angry": EmotionType.ANGRY,
                    "surprised": EmotionType.SURPRISED,
                    "love": EmotionType.LOVE,
                }
                return emotion_map.get(emotion_str, EmotionType.NEUTRAL)
            else:
                self.logger.warning("OpenAI API 返回了无效的响应结构")
                return EmotionType.NEUTRAL

        except Exception as e:
            self.logger.error(f"调用 OpenAI API 时发生错误: {e}，使用规则判断", exc_info=True)
            return self._judge_emotion_by_rules(text)

    def _judge_emotion_by_rules(self, text: str) -> EmotionType:
        """使用规则判断情感（降级方案）"""
        emotion_keywords = {
            EmotionType.HAPPY: ["开心", "高兴", "哈哈", "快乐", "笑", "😊", "😄", "🎉"],
            EmotionType.SAD: ["难过", "伤心", "哭", "😢", "😭", "💔"],
            EmotionType.ANGRY: ["生气", "愤怒", "😠", "😡", "🔥"],
            EmotionType.SURPRISED: ["惊讶", "意外", "哇", "😲", "😱"],
            EmotionType.LOVE: ["爱", "喜欢", "❤️", "💕", "😍"],
        }

        for emotion, keywords in emotion_keywords.items():
            if any(keyword in text for keyword in keywords):
                return emotion

        return EmotionType.NEUTRAL

    def _create_actions_for_emotion(self, emotion: EmotionType) -> list:
        """根据情感创建动作列表"""
        actions = []

        # 根据情感添加默认动作
        if emotion == EmotionType.HAPPY:
            actions.append(IntentAction(type=ActionType.EXPRESSION, params={"name": "smile"}, priority=70))
        elif emotion == EmotionType.SAD:
            actions.append(IntentAction(type=ActionType.EXPRESSION, params={"name": "sad"}, priority=70))
        elif emotion == EmotionType.ANGRY:
            actions.append(IntentAction(type=ActionType.EXPRESSION, params={"name": "angry"}, priority=70))
        elif emotion == EmotionType.SURPRISED:
            actions.append(IntentAction(type=ActionType.EXPRESSION, params={"name": "surprised"}, priority=70))
            actions.append(IntentAction(type=ActionType.BLINK, params={}, priority=50))
        elif emotion == EmotionType.LOVE:
            actions.append(IntentAction(type=ActionType.EXPRESSION, params={"name": "love"}, priority=70))

        # 如果没有特定动作，添加眨眼
        if not actions:
            actions.append(IntentAction(type=ActionType.BLINK, params={}, priority=30))

        return actions
