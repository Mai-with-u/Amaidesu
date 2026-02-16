"""
Warudo Provider 工具模块
"""

from typing import Dict

from src.modules.types import EmotionType


class IntentEmotionAdapter:
    """适配器：将 EmotionType 转换为 Warudo 需要的情绪字典格式"""

    # Warudo 使用的情绪格式（参考旧插件 MaiCore 格式）
    # 默认值为 1-10 的整数
    _DEFAULT_MOOD: Dict[str, int] = {
        "joy": 5,
        "anger": 1,
        "sorrow": 1,
        "fear": 1,
    }

    # EmotionType 到 Warudo 情绪的映射
    # 每个 EmotionType 对应一组 joy/anger/sorrow/fear 的值
    _EMOTION_MAPPING: Dict[EmotionType, Dict[str, int]] = {
        EmotionType.NEUTRAL: {"joy": 5, "anger": 1, "sorrow": 1, "fear": 1},
        EmotionType.HAPPY: {"joy": 7, "anger": 1, "sorrow": 1, "fear": 1},
        EmotionType.SAD: {"joy": 1, "anger": 1, "sorrow": 7, "fear": 1},
        EmotionType.ANGRY: {"joy": 1, "anger": 7, "sorrow": 1, "fear": 1},
        EmotionType.SURPRISED: {"joy": 6, "anger": 1, "sorrow": 1, "fear": 6},
        EmotionType.LOVE: {"joy": 8, "anger": 1, "sorrow": 1, "fear": 1},
        EmotionType.SHY: {"joy": 5, "anger": 1, "sorrow": 2, "fear": 4},
        EmotionType.EXCITED: {"joy": 9, "anger": 1, "sorrow": 1, "fear": 1},
        EmotionType.CONFUSED: {"joy": 3, "anger": 2, "sorrow": 2, "fear": 5},
        EmotionType.SCARED: {"joy": 1, "anger": 1, "sorrow": 2, "fear": 9},
    }

    @staticmethod
    def adapt(emotion: EmotionType) -> Dict[str, int]:
        """
        将 EmotionType 转换为情绪字典

        Args:
            emotion: EmotionType 枚举

        Returns:
            {"joy": 5, "anger": 1, "sorrow": 1, "fear": 1} 格式的字典
        """
        # 从映射表中获取对应的情绪值
        mood_mapping = IntentEmotionAdapter._EMOTION_MAPPING.get(
            emotion,
            IntentEmotionAdapter._DEFAULT_MOOD,
        )

        # 返回副本避免修改原始映射
        return mood_mapping.copy()
