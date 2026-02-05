"""
Emotion Judge Decision Provider
"""

from .emotion_judge_decision_provider import EmotionJudgeDecisionProvider
from src.core.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_decision("emotion_judge", EmotionJudgeDecisionProvider, source="builtin:emotion_judge")

__all__ = ["EmotionJudgeDecisionProvider"]
