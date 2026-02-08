"""
动作映射器 - Output Domain: 参数生成

职责:
- 将Intent中的ActionType映射到渲染指令
- 支持多种动作类型（热键、表情、TTS等）
- 生成可执行的渲染参数
"""

from typing import Dict, Any, List
from src.domains.decision.intent import ActionType, IntentAction


class ActionMapper:
    """
    动作映射器

    职责:
    - 将IntentAction映射到渲染指令
    - 支持多种动作类型（热键、表情、道具等）
    - 生成对应的ExpressionParameters
    """

    # 默认动作类型处理函数映射
    DEFAULT_ACTION_HANDLERS = {
        ActionType.EMOJI: "handle_emoji_action",
        ActionType.HOTKEY: "handle_hotkey_action",
        ActionType.EXPRESSION: "handle_expression_action",
        ActionType.BLINK: "handle_blink_action",
        ActionType.NOD: "handle_nod_action",
        ActionType.SHAKE: "handle_shake_action",
        ActionType.WAVE: "handle_wave_action",
        ActionType.CLAP: "handle_clap_action",
    }

    def __init__(self):
        """
        初始化动作映射器
        """
        pass

    def _get_expression_map(self) -> Dict[str, Dict[str, Any]]:
        """
        获取语义名称到VTS参数的映射表

        Returns:
            语义名称 → VTS参数字典的映射
        """
        return {
            "smile": {"mouth_opening": 0.5, "cheek_puffing": 0.2},
            "sad": {"eyebrow_troubled_left": 0.3, "eyebrow_troubled_right": 0.3, "mouth_opening": 0.3},
            "angry": {"eyebrow_troubled_left": 0.8, "eyebrow_troubled_right": 0.8},
            "surprised": {"mouth_opening": 0.8, "eye_opening": 0.8},
            "neutral": {},
        }

    def map_actions(self, actions: List[IntentAction]) -> Dict[str, Any]:
        """
        将动作列表映射为渲染参数

        Args:
            actions: IntentAction列表

        Returns:
            渲染参数字典，包含:
            - hotkeys: 热键列表
            - actions: 动作列表
            - expressions: 表情参数字典
        """
        result = {
            "hotkeys": [],
            "actions": [],
            "expressions": {},
        }

        # 按优先级排序
        sorted_actions = sorted(actions, key=lambda a: a.priority)

        for action in sorted_actions:
            handler_name = self.DEFAULT_ACTION_HANDLERS.get(action.type)
            if handler_name and hasattr(self, handler_name):
                handler = getattr(self, handler_name)
                handler(action, result)

        return result

    def handle_text_action(self, action: IntentAction, result: Dict[str, Any]):
        """
        处理文本动作

        Args:
            action: IntentAction
            result: 结果字典
        """
        # 文本动作通常不需要特殊处理，response_text已经在ExpressionParameters中
        pass

    def handle_emoji_action(self, action: IntentAction, result: Dict[str, Any]):
        """
        处理表情动作

        Args:
            action: IntentAction
            result: 结果字典
        """
        # 表情动作可以作为actions的一部分传递
        result["actions"].append(
            {
                "type": "emoji",
                "params": action.params,
            }
        )

    def handle_hotkey_action(self, action: IntentAction, result: Dict[str, Any]):
        """
        处理热键动作

        Args:
            action: IntentAction
            result: 结果字典
        """
        # 兼容两种参数名: "key" (LLM生成的语义格式) 和 "hotkey_id" (VTS格式)
        hotkey_id = action.params.get("key") or action.params.get("hotkey_id")
        if hotkey_id:
            result["hotkeys"].append(hotkey_id)

    def handle_tts_action(self, action: IntentAction, result: Dict[str, Any]):
        """
        处理TTS动作

        Args:
            action: IntentAction
            result: 结果字典
        """
        # TTS动作通过enabled标志控制，params可以包含额外配置
        result["actions"].append(
            {
                "type": "tts",
                "params": action.params,
            }
        )

    def handle_subtitle_action(self, action: IntentAction, result: Dict[str, Any]):
        """
        处理字幕动作

        Args:
            action: IntentAction
            result: 结果字典
        """
        # 字幕动作通过enabled标志控制，params可以包含额外配置
        result["actions"].append(
            {
                "type": "subtitle",
                "params": action.params,
            }
        )

    def handle_expression_action(self, action: IntentAction, result: Dict[str, Any]):
        """
        处理表情动作

        Args:
            action: IntentAction
            result: 结果字典
        """
        # 支持 {"name": "smile"} 语义格式（LLM生成）
        name = action.params.get("name")
        if name:
            # 语义名称 → VTS 参数映射
            expression_map = self._get_expression_map()
            if name in expression_map:
                result["expressions"].update(expression_map[name])

        # 也支持直接传递 VTS 参数格式
        expressions = action.params.get("expressions", {})
        result["expressions"].update(expressions)

    def handle_motion_action(self, action: IntentAction, result: Dict[str, Any]):
        """
        处理动作动作

        Args:
            action: IntentAction
            result: 结果字典
        """
        # 动作作为actions的一部分传递
        result["actions"].append(
            {
                "type": "motion",
                "params": action.params,
            }
        )

    def handle_blink_action(self, action: IntentAction, result: Dict[str, Any]):
        """
        处理眨眼动作

        Args:
            action: IntentAction
            result: 结果字典
        """
        result["actions"].append(
            {
                "type": "blink",
                "params": {"duration": 0.15},
            }
        )

    def handle_nod_action(self, action: IntentAction, result: Dict[str, Any]):
        """
        处理点头动作

        Args:
            action: IntentAction
            result: 结果字典
        """
        result["actions"].append(
            {
                "type": "nod",
                "params": {"intensity": 0.5},
            }
        )

    def handle_shake_action(self, action: IntentAction, result: Dict[str, Any]):
        """
        处理摇头动作

        Args:
            action: IntentAction
            result: 结果字典
        """
        result["actions"].append(
            {
                "type": "shake",
                "params": {"intensity": 0.4},
            }
        )

    def handle_wave_action(self, action: IntentAction, result: Dict[str, Any]):
        """
        处理挥手动作

        Args:
            action: IntentAction
            result: 结果字典
        """
        hotkey = action.params.get("hotkey", "wave_animation")
        result["hotkeys"].append(hotkey)

    def handle_clap_action(self, action: IntentAction, result: Dict[str, Any]):
        """
        处理鼓掌动作

        Args:
            action: IntentAction
            result: 结果字典
        """
        hotkey = action.params.get("hotkey", "clap_animation")
        result["hotkeys"].append(hotkey)

    def handle_custom_action(self, action: IntentAction, result: Dict[str, Any]):
        """
        处理自定义动作

        Args:
            action: IntentAction
            result: 结果字典
        """
        # 自定义动作直接传递
        result["actions"].append(
            {
                "type": "custom",
                "params": action.params,
            }
        )
