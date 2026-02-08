"""
情感映射器 - Output Domain: 参数生成

职责:
- 将Intent中的EmotionType映射到VTS表情参数
- 支持常见情感的预设表情
- 可扩展的情感到表情参数映射
"""

from typing import Dict
from src.domains.decision.intent import EmotionType


class EmotionMapper:
    """
    情感映射器

    职责:
    - 将EmotionType映射到VTS表情参数
    - 支持自定义情感映射
    - 提供默认的情感表情预设
    """

    # 默认情感到VTS表情参数的映射
    DEFAULT_EMOTION_MAP: Dict[EmotionType, Dict[str, float]] = {
        EmotionType.NEUTRAL: {
            "MouthSmile": 0.0,
            "EyeOpenLeft": 1.0,
            "EyeOpenRight": 1.0,
            "BrowLeftY": 0.0,
            "BrowRightY": 0.0,
        },
        EmotionType.HAPPY: {
            "MouthSmile": 0.8,
            "EyeOpenLeft": 0.85,
            "EyeOpenRight": 0.85,
            "BrowLeftY": 0.2,
            "BrowRightY": 0.2,
        },
        EmotionType.SAD: {
            "MouthSmile": -0.3,
            "EyeOpenLeft": 0.7,
            "EyeOpenRight": 0.7,
            "BrowLeftY": -0.4,
            "BrowRightY": -0.4,
        },
        EmotionType.ANGRY: {
            "MouthSmile": -0.2,
            "EyeOpenLeft": 0.9,
            "EyeOpenRight": 0.9,
            "BrowLeftY": -0.6,
            "BrowRightY": -0.6,
        },
        EmotionType.SURPRISED: {
            "MouthSmile": 0.1,
            "MouthOpen": 0.5,
            "EyeOpenLeft": 1.2,
            "EyeOpenRight": 1.2,
            "BrowLeftY": 0.5,
            "BrowRightY": 0.5,
        },
        EmotionType.LOVE: {
            "MouthSmile": 0.7,
            "EyeOpenLeft": 0.8,
            "EyeOpenRight": 0.8,
            "CheekPuff": 0.3,
        },
        EmotionType.SHY: {
            "MouthSmile": 0.3,
            "EyeOpenLeft": 0.6,
            "EyeOpenRight": 0.6,
            "CheekPuff": 0.5,
            "BrowLeftY": -0.2,
            "BrowRightY": -0.2,
        },
        EmotionType.EXCITED: {
            "MouthSmile": 1.0,
            "MouthOpen": 0.3,
            "EyeOpenLeft": 1.1,
            "EyeOpenRight": 1.1,
            "BrowLeftY": 0.4,
            "BrowRightY": 0.4,
        },
        EmotionType.CONFUSED: {
            "MouthSmile": 0.0,
            "EyeOpenLeft": 0.9,
            "EyeOpenRight": 0.75,
            "BrowLeftY": 0.3,
            "BrowRightY": -0.2,
        },
        EmotionType.SCARED: {
            "MouthOpen": 0.4,
            "EyeOpenLeft": 1.3,
            "EyeOpenRight": 1.3,
            "BrowLeftY": 0.6,
            "BrowRightY": 0.6,
        },
    }

    def __init__(self, emotion_map: Dict[EmotionType, Dict[str, float]] = None):
        """
        初始化情感映射器

        Args:
            emotion_map: 自定义情感映射表，如果为None则使用默认映射
        """
        self.emotion_map = emotion_map or self.DEFAULT_EMOTION_MAP.copy()

    def map_emotion(self, emotion: EmotionType) -> Dict[str, float]:
        """
        将情感类型映射到VTS表情参数

        Args:
            emotion: 情感类型

        Returns:
            VTS表情参数字典
        """
        return self.emotion_map.get(emotion, self.DEFAULT_EMOTION_MAP[EmotionType.NEUTRAL]).copy()

    def set_emotion_mapping(self, emotion: EmotionType, params: Dict[str, float]):
        """
        设置情感映射

        Args:
            emotion: 情感类型
            params: VTS表情参数字典
        """
        self.emotion_map[emotion] = params.copy()

    def add_emotion(self, emotion_name: str, params: Dict[str, float]):
        """
        添加新的情感类型（作为扩展）

        Args:
            emotion_name: 情感名称
            params: VTS表情参数字典
        """
        try:
            emotion = EmotionType(emotion_name.lower())
            self.emotion_map[emotion] = params.copy()
        except ValueError:
            # 不是有效的EmotionType，记录警告
            pass

    def get_available_emotions(self) -> list:
        """
        获取所有可用的情感类型

        Returns:
            情感类型列表
        """
        return list(self.emotion_map.keys())
