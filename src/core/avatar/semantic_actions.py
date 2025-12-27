"""
语义动作映射器

将高层次的语义动作（如 "happy_expression"）映射到平台特定的参数。
支持平台特定的覆盖和强度系数调整。
"""

from typing import Dict, Any, Optional


class SemanticActionMapper:
    """语义动作映射器

    将高层次的语义动作映射到平台特定的参数值。
    例如：happy_expression → {MouthSmile: 1.0, EyeOpenLeft: 0.9}

    支持功能：
    1. 默认语义动作定义
    2. 平台特定的参数覆盖
    3. 强度系数调整
    """

    # 默认语义动作定义
    DEFAULT_SEMANTIC_ACTIONS = {
        "happy_expression": {
            "description": "开心的表情",
            "default_mapping": {
                "MouthSmile": 1.0,
                "EyeOpenLeft": 0.9,
                "EyeOpenRight": 0.9,
            },
        },
        "sad_expression": {
            "description": "悲伤的表情",
            "default_mapping": {
                "MouthSmile": -0.5,
                "EyeOpenLeft": 0.5,
                "EyeOpenRight": 0.5,
                "MouthOpen": 0.2,
            },
        },
        "surprised_expression": {
            "description": "惊讶的表情",
            "default_mapping": {
                "MouthOpen": 0.8,
                "EyeOpenLeft": 1.0,
                "EyeOpenRight": 1.0,
            },
        },
        "angry_expression": {
            "description": "生气的表情",
            "default_mapping": {
                "MouthSmile": -0.8,
                "EyeOpenLeft": 0.6,
                "EyeOpenRight": 0.6,
            },
        },
        "close_eyes": {
            "description": "闭眼",
            "default_mapping": {
                "EyeOpenLeft": 0.0,
                "EyeOpenRight": 0.0,
            },
        },
        "open_eyes": {
            "description": "睁眼",
            "default_mapping": {
                "EyeOpenLeft": 1.0,
                "EyeOpenRight": 1.0,
            },
        },
        "neutral": {
            "description": "中性表情",
            "default_mapping": {
                "MouthSmile": 0.0,
                "MouthOpen": 0.0,
                "EyeOpenLeft": 1.0,
                "EyeOpenRight": 1.0,
            },
        },
    }

    def __init__(self, custom_mappings: Optional[Dict[str, Any]] = None):
        """
        初始化映射器

        Args:
            custom_mappings: 用户自定义的语义动作映射
        """
        # 复制默认动作
        self.semantic_actions = {}
        for action_name, action_data in self.DEFAULT_SEMANTIC_ACTIONS.items():
            self.semantic_actions[action_name] = {
                "description": action_data["description"],
                "default_mapping": action_data["default_mapping"].copy(),
            }

        # 合并自定义映射
        if custom_mappings:
            for action_name, mapping in custom_mappings.items():
                if action_name in self.semantic_actions:
                    # 更新现有动作的描述
                    if "description" in mapping:
                        self.semantic_actions[action_name]["description"] = mapping["description"]
                    # 如果有 default_mapping，更新它
                    if "default_mapping" in mapping:
                        self.semantic_actions[action_name]["default_mapping"].update(mapping["default_mapping"])
                else:
                    # 添加新动作
                    self.semantic_actions[action_name] = {
                        "description": mapping.get("description", ""),
                        "default_mapping": mapping.get("default_mapping", {}).copy(),
                    }

        # 支持平台特定的映射覆盖
        # 格式：{platform: {action_name: {param: value}}}
        self.platform_overrides: Dict[str, Dict[str, Dict[str, float]]] = {}

    def add_platform_override(self, platform: str, action_name: str, mapping: Dict[str, float]) -> None:
        """添加平台特定的映射覆盖

        Args:
            platform: 平台名称（如 "vts", "vrc", "live2d"）
            action_name: 语义动作名称
            mapping: 参数名到值的映射
        """
        if platform not in self.platform_overrides:
            self.platform_overrides[platform] = {}
        self.platform_overrides[platform][action_name] = mapping.copy()

    def set_platform_overrides(self, platform: str, overrides: Dict[str, Dict[str, float]]) -> None:
        """批量设置平台特定的映射覆盖

        Args:
            platform: 平台名称
            overrides: {action_name: {param: value}} 的映射
        """
        if platform not in self.platform_overrides:
            self.platform_overrides[platform] = {}
        self.platform_overrides[platform].update(overrides)

    def get_mapping(self, action_name: str, platform: str = None) -> Dict[str, float]:
        """获取语义动作的参数映射

        Args:
            action_name: 语义动作名称
            platform: 平台名称（可选）

        Returns:
            参数名到值的映射，如果未找到返回空字典
        """
        # 检查平台特定覆盖
        if platform and platform in self.platform_overrides:
            if action_name in self.platform_overrides[platform]:
                return self.platform_overrides[platform][action_name].copy()

        # 返回默认映射
        if action_name in self.semantic_actions:
            return self.semantic_actions[action_name]["default_mapping"].copy()

        return {}

    def list_semantic_actions(self) -> Dict[str, str]:
        """列出所有语义动作

        Returns:
            动作名到描述的映射
        """
        return {name: data.get("description", "") for name, data in self.semantic_actions.items()}

    def has_action(self, action_name: str) -> bool:
        """检查是否存在指定的语义动作

        Args:
            action_name: 动作名称

        Returns:
            是否存在
        """
        return action_name in self.semantic_actions

    def apply_intensity(self, mapping: Dict[str, float], intensity: float) -> Dict[str, float]:
        """应用强度系数到参数映射

        Args:
            mapping: 参数名到值的映射
            intensity: 强度系数 (0.0-1.0)

        Returns:
            应用强度后的参数映射
        """
        return {param_name: param_value * intensity for param_name, param_value in mapping.items()}

    def get_action_description(self, action_name: str) -> str:
        """获取语义动作的描述

        Args:
            action_name: 动作名称

        Returns:
            动作描述，如果不存在返回空字符串
        """
        if action_name in self.semantic_actions:
            return self.semantic_actions[action_name]["description"]
        return ""
