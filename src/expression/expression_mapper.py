"""
Expression Mapper - Layer 6: 表情映射（统一入口）

职责:
- 统一的表情映射器（Layer 6 唯一位置）
- 合并原有的 EmotionMapper 和 SemanticActionMapper
- 输出平台无关的抽象参数（由 PlatformAdapter 翻译为平台特定参数）
- 支持自定义情感映射
"""

from typing import Dict, Optional

from src.understanding.intent import EmotionType
from src.utils.logger import get_logger


class ExpressionMapper:
    """统一的表情映射器

    合并原来的：
    - Layer 6 的 EmotionMapper
    - Avatar 的 SemanticActionMapper

    输出平台无关的抽象参数，由 PlatformAdapter 翻译为平台特定参数。
    """

    # 平台无关的抽象参数定义
    # 这些参数会被 PlatformAdapter 翻译为平台特定参数
    DEFAULT_ABSTRACT_MAPPINGS: Dict[EmotionType, Dict[str, float]] = {
        EmotionType.NEUTRAL: {"smile": 0.0, "eye_open": 1.0},
        EmotionType.HAPPY: {"smile": 0.8, "eye_open": 0.9},
        EmotionType.SAD: {"smile": -0.3, "eye_open": 0.7},
        EmotionType.ANGRY: {"smile": -0.5, "eye_open": 0.6, "brow_down": 0.5},
        EmotionType.SURPRISED: {"smile": 0.2, "eye_open": 1.0, "mouth_open": 0.6},
        EmotionType.EXCITED: {"smile": 0.6, "eye_open": 0.95},
        EmotionType.CONFUSED: {"smile": 0.2, "eye_open": 0.85},
        EmotionType.LOVE: {"smile": 0.7, "eye_open": 0.95},
    }

    def __init__(self, custom_mappings: Optional[Dict[EmotionType, Dict[str, float]]] = None):
        """
        初始化表情映射器

        Args:
            custom_mappings: 自定义情感映射（覆盖默认映射）
        """
        self.logger = get_logger("ExpressionMapper")

        # 复制默认映射
        self.mappings = {}
        for emotion, params in self.DEFAULT_ABSTRACT_MAPPINGS.items():
            self.mappings[emotion] = params.copy()

        # 合并自定义映射
        if custom_mappings:
            for emotion, params in custom_mappings.items():
                if emotion in self.mappings:
                    self.mappings[emotion].update(params)
                else:
                    self.mappings[emotion] = params.copy()

        self.logger.info(f"ExpressionMapper 初始化完成，支持 {len(self.mappings)} 种情感")

    def map_emotion(self, emotion: EmotionType, intensity: float = 1.0) -> Dict[str, float]:
        """
        情感 → 抽象表情参数

        Args:
            emotion: 情感类型
            intensity: 强度 (0.0-1.0)

        Returns:
            抽象表情参数字典（平台无关）
        """
        base_params = self.mappings.get(emotion, self.mappings[EmotionType.NEUTRAL])
        return {k: v * intensity for k, v in base_params.items()}

    def set_emotion_mapping(self, emotion: EmotionType, params: Dict[str, float]) -> None:
        """
        设置情感映射

        Args:
            emotion: 情感类型
            params: 抽象参数字典
        """
        self.mappings[emotion] = params.copy()
        self.logger.debug(f"情感映射已更新: {emotion}")

    def get_available_emotions(self) -> list[EmotionType]:
        """
        获取所有可用的情感类型

        Returns:
            情感类型列表
        """
        return list(self.mappings.keys())

    # ==================== 向后兼容方法 ====================

    def add_emotion(self, emotion_name: str, params: Dict[str, float]) -> None:
        """
        添加新的情感类型（向后兼容）

        Args:
            emotion_name: 情感名称
            params: 抽象参数字典
        """
        try:
            emotion = EmotionType(emotion_name.lower())
            self.mappings[emotion] = params.copy()
            self.logger.debug(f"添加新情感: {emotion_name}")
        except ValueError:
            self.logger.warning(f"无效的情感类型: {emotion_name}")
