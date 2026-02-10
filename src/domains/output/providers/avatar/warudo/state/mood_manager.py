"""心情状态管理器

根据情绪数据自动选择和应用合适的表情组合。
"""

import logging
import random
from typing import Dict, Any


class MoodManager:
    """心情状态管理器

    根据情绪数据（joy, anger, sorrow, fear）自动选择和应用合适的表情组合。
    """

    def __init__(self, state_manager, logger: logging.Logger):
        """初始化心情管理器

        Args:
            state_manager: WarudoStateManager 实例
            logger: 日志记录器
        """
        self.state_manager = state_manager
        self.logger = logger

        # 当前心情状态 (1-10)
        self.current_mood = {"joy": 5, "anger": 1, "sorrow": 1, "fear": 1}

        # 表情组合模板
        self.expression_combinations = {
            "happy": {
                "eye": {"action": "", "weight": 1},
                "eyebrow": {"action": "eyebrow_happy_weak", "weight": 0.8},
                "mouth": {"action": "mouth_happy_strong", "weight": 0.3},
            },
            "happy_2": {
                "eye": {"action": "", "weight": 1},
                "eyebrow": {"action": "eyebrow_happy_strong", "weight": 1},
                "mouth": {"action": "mouth_smlie_2", "weight": 0.2},
            },
            "very_happy": {
                "eye": {"action": "eye_happy_strong", "weight": 1},
                "eyebrow": {"action": "eyebrow_happy_strong", "weight": 1},
                "mouth": {"action": "mouth_happy_strong", "weight": 0.57},
            },
            "very_happy_2": {
                "eye": {"action": "", "weight": 1},
                "eyebrow": {"action": "eyebrow_happy_strong", "weight": 1},
                "mouth": {"action": "mouth_smlie_3", "weight": 0.63},
            },
            "sad": {
                "eye": {"action": "", "weight": 1},
                "eyebrow": {"action": "eyebrow_sad_weak", "weight": 1},
                "mouth": {"action": "mouth_sad_weak", "weight": 1},
            },
            "angry": {
                "eye": {"action": "", "weight": 1},
                "eyebrow": {"action": "eyebrow_angry_strong", "weight": 1},
                "mouth": {"action": "mouth_angry_weak", "weight": 0.8},
            },
            "angry_2": {
                "eye": {"action": "", "weight": 1},
                "eyebrow": {"action": "eyebrow_angry_strong", "weight": 0.81},
                "mouth": {"action": "mouth_happy_strong", "weight": 0.18},
            },
            "very_angry": {
                "eye": {"action": "", "weight": 1},
                "eyebrow": {"action": "eyebrow_angry_weak", "weight": 0.8},
                "mouth": {"action": "mouth_angry_weak", "weight": 0.7},
            },
            "neutral": {
                "eye": {"action": "", "weight": 0},
                "eyebrow": {"action": "", "weight": 0},
                "mouth": {"action": "", "weight": 0},
            },
        }

        # 当前表情状态
        self.current_expression = "neutral"

        self.logger.info("心情状态管理器已初始化")

    def update_mood(self, mood_data: Dict[str, Any]) -> bool:
        """更新心情状态

        Args:
            mood_data: 包含心情数据的字典，格式为 {"joy": 5, "anger": 1, "sorrow": 1, "fear": 1}

        Returns:
            bool: 是否有变化发生
        """
        has_changes = False

        for emotion in ["joy", "anger", "sorrow", "fear"]:
            new_value = max(1, min(10, int(mood_data[emotion])))
            if self.current_mood[emotion] != new_value:
                self.current_mood[emotion] = new_value
                has_changes = True

        if has_changes:
            self.logger.info(f"心情状态已更新: {self.current_mood}")
            # 根据新心情更新表情
            self._update_expression_by_mood()

        return has_changes

    def _update_expression_by_mood(self):
        """根据当前心情更新表情"""
        try:
            # 选择合适的表情
            new_expression = self._select_expression_by_mood()

            if new_expression != self.current_expression:
                self.logger.info(f"表情变化: {self.current_expression} → {new_expression}")
                self.current_expression = new_expression

                # 应用新表情
                self._apply_expression(new_expression)
            else:
                self.logger.debug(f"心情更新，但表情保持不变: {new_expression}")

        except Exception as e:
            self.logger.error(f"根据心情更新表情时出错: {e}", exc_info=True)

    def _select_expression_by_mood(self) -> str:
        """根据情绪值选择合适的表情组合

        Returns:
            str: 表情名称
        """
        # 获取最强情绪
        dominant_emotion = max(self.current_mood, key=self.current_mood.get)
        dominant_value = self.current_mood[dominant_emotion]

        self.logger.info(
            f"情绪分析: joy={self.current_mood['joy']}, "
            f"anger={self.current_mood['anger']}, "
            f"sorrow={self.current_mood['sorrow']}, "
            f"fear={self.current_mood['fear']}"
        )
        self.logger.info(f"主导情绪: {dominant_emotion}({dominant_value})")

        # 根据情绪强度和类型选择表情
        if dominant_emotion == "joy":
            if self.current_mood["joy"] >= 9:
                return "very_happy"
            elif self.current_mood["joy"] >= 7:
                return "happy"
            else:
                return "neutral"
        elif dominant_emotion == "anger":
            if self.current_mood["anger"] >= 7:
                return "very_angry"
            elif self.current_mood["anger"] >= 5:
                return "angry"
            else:
                return "neutral"
        elif dominant_emotion == "sorrow" and self.current_mood["sorrow"] >= 5:
            return "sad"
        else:
            return "neutral"

    def _apply_expression(self, expression_name: str):
        """应用指定的表情组合

        Args:
            expression_name: 表情名称
        """
        try:
            # 随机选择同前缀的表情组合
            candidates = [k for k in self.expression_combinations if k.startswith(expression_name)]
            if len(candidates) > 1:
                chosen = random.choice(candidates)
                self.logger.info(f"表情前缀 '{expression_name}' 有多个候选，随机选择: {chosen}")
                expression_name = chosen

            if expression_name not in self.expression_combinations:
                self.logger.warning(f"未知的表情: {expression_name}")
                return

            expression_config = self.expression_combinations[expression_name]

            # 应用新表情配置
            for part, action in expression_config.items():
                weight = action.get("weight", 1)
                action_value = action.get("action", "")
                self.logger.info(f"应用表情 '{expression_name}' 的 {part} 状态: {action_value} (权重: {weight})")
                if part == "eye":
                    self.state_manager.eye_state.set_first_layer(action_value, weight=weight)
                elif part == "eyebrow":
                    self.state_manager.eyebrow_state.set_first_layer(action_value, weight=weight)
                elif part == "mouth":
                    self.state_manager.mouth_state.set_first_layer(action_value, weight=weight)

            self.logger.info(f"应用表情 '{expression_name}'")

        except Exception as e:
            self.logger.error(f"应用表情 '{expression_name}' 时出错: {e}", exc_info=True)
