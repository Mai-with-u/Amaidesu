"""
LLM 工具生成器

根据注册的参数和动作生成符合 OpenAI 工具调用格式的定义。
使用 OpenAI 官方类型定义，确保类型安全和 IDE 支持。
"""

from typing import Dict, List, Any, Optional
from openai.types.chat import ChatCompletionToolParam

from .adapter_base import ParameterMetadata, ActionMetadata


class ToolGenerator:
    """LLM 工具生成器

    将注册的参数和动作转换为 OpenAI Function Calling 格式的工具定义。
    生成的工具可以让 LLM 理解并调用虚拟形象控制功能。

    使用 OpenAI 官方类型定义，提供完整的类型检查和 IDE 自动补全。
    """

    def generate_tools(
        self,
        parameters: Dict[str, ParameterMetadata],
        actions: Dict[str, ActionMetadata],
        semantic_actions: Optional[List[str]] = None,
    ) -> List[ChatCompletionToolParam]:
        """生成完整的工具列表

        Args:
            parameters: 参数名到元数据的映射
            actions: 动作名到元数据的映射
            semantic_actions: 语义动作名称列表（可选）

        Returns:
            符合 OpenAI 类型的工具定义列表
        """
        tools: List[ChatCompletionToolParam] = []

        # 1. 语义动作工具（表情控制）
        if semantic_actions:
            tools.append(self._generate_semantic_actions_tool(semantic_actions))

        # 2. 直接参数控制工具
        if parameters:
            tools.append(self._generate_parameter_control_tool(parameters))

        # 3. 平台动作触发工具
        if actions:
            tools.append(self._generate_action_trigger_tool(actions))

        return tools

    def _generate_semantic_actions_tool(self, semantic_actions: List[str]) -> ChatCompletionToolParam:
        """生成语义动作工具

        Args:
            semantic_actions: 语义动作名称列表

        Returns:
            符合 OpenAI 类型的表情控制工具定义
        """
        # 构建表情枚举值和描述
        enum_values: List[str] = []
        description_parts: List[str] = []

        # 动作描述映射
        action_descriptions = {
            "happy_expression": "开心 - 微笑，眼睛明亮",
            "sad_expression": "悲伤 - 嘴角下垂，眼神柔和",
            "surprised_expression": "惊讶 - 张嘴，瞪眼",
            "angry_expression": "生气 - 皱眉，眼神严厉",
            "close_eyes": "闭眼 - 闭上一双眼睛",
            "open_eyes": "睁眼 - 睁开一双眼睛",
            "neutral": "中性 - 恢复到默认表情",
        }

        for action in semantic_actions:
            if action in enum_values:
                continue
            enum_values.append(action)
            desc = action_descriptions.get(action, action)
            description_parts.append(f"- {action}: {desc}")

        # 构建符合 OpenAI 类型的工具定义
        tool: ChatCompletionToolParam = {
            "type": "function",
            "function": {
                "name": "set_avatar_expression",
                "description": (
                    "设置虚拟形象的表情和神态。支持多种预定义的表情动作。\n\n"
                    "可用表情：\n" + "\n".join(description_parts) + "\n\n"
                    "使用 intensity 参数控制表情的强度（0.0-1.0）。\n"
                    "例如：开心表情可以只微笑一点点（intensity=0.3）或开怀大笑（intensity=1.0）。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "enum": enum_values,
                            "description": "要设置的表情类型"
                        },
                        "intensity": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "description": "表情的强度，范围 0.0-1.0。0.0 为最弱，1.0 为最强。",
                        },
                    },
                    "required": ["expression"],
                },
            },
        }

        return tool

    def _generate_parameter_control_tool(self, parameters: Dict[str, ParameterMetadata]) -> ChatCompletionToolParam:
        """生成直接参数控制工具

        Args:
            parameters: 参数名到元数据的映射

        Returns:
            符合 OpenAI 类型的参数控制工具定义
        """
        # 按分类组织参数
        by_category: Dict[str, List[tuple]] = {}
        for param_name, metadata in parameters.items():
            category = metadata.category
            if category not in by_category:
                by_category[category] = []
            by_category[category].append((param_name, metadata))

        # 构建参数属性
        properties: Dict[str, Dict[str, Any]] = {}

        for param_name, metadata in parameters.items():
            param_spec = {
                "type": "number",
                "description": metadata.description or metadata.display_name,
                "minimum": float(metadata.min_value),
                "maximum": float(metadata.max_value),
            }
            properties[param_name] = param_spec

        # 构建分类说明
        category_descriptions: List[str] = []
        for category, params in by_category.items():
            param_names = [name for name, _ in params]
            category_descriptions.append(
                f"- {category}: {', '.join(param_names[:5])}"
                + (f" ... ({len(params)}个参数)" if len(params) > 5 else "")
            )

        # 构建符合 OpenAI 类型的工具定义
        tool: ChatCompletionToolParam = {
            "type": "function",
            "function": {
                "name": "set_avatar_parameters",
                "description": (
                    "直接控制虚拟形象的各项参数。\n\n"
                    "可以同时设置多个参数，所有参数都是可选的。\n\n"
                    "参数分类：\n" + "\n".join(category_descriptions) + "\n\n"
                    "注意：这是直接参数控制，通常用于精细调整。"
                    "对于常见表情，建议使用 set_avatar_expression 工具。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "description": "要设置的参数及其值",
                },
            },
        }

        return tool

    def _generate_action_trigger_tool(self, actions: Dict[str, ActionMetadata]) -> ChatCompletionToolParam:
        """生成动作触发工具

        Args:
            actions: 动作名到元数据的映射

        Returns:
            符合 OpenAI 类型的动作触发工具定义
        """
        # 按分类组织动作
        by_category: Dict[str, List[tuple]] = {}
        for action_name, metadata in actions.items():
            category = metadata.category
            if category not in by_category:
                by_category[category] = []
            by_category[category].append((action_name, metadata))

        # 构建参数属性
        properties: Dict[str, Dict[str, Any]] = {}
        for action_name, metadata in actions.items():
            properties[action_name] = {
                "type": "boolean",
                "description": f"{metadata.display_name} - {metadata.description}",
            }

        # 构建分类说明
        category_descriptions: List[str] = []
        for category, action_list in by_category.items():
            action_names = [name for name, _ in action_list]
            category_descriptions.append(
                f"- {category}: {', '.join(action_names[:5])}"
                + (f" ... ({len(action_list)}个动作)" if len(action_list) > 5 else "")
            )

        # 构建符合 OpenAI 类型的工具定义
        tool: ChatCompletionToolParam = {
            "type": "function",
            "function": {
                "name": "trigger_avatar_action",
                "description": (
                    "触发虚拟形象的预设动作或手势。\n\n"
                    "通过将对应动作设置为 true 来触发。\n"
                    "一次只能触发一个动作。\n\n"
                    "动作分类：\n" + "\n".join(category_descriptions)
                ),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "description": "要触发的动作（设置为 true 触发）",
                },
            },
        }

        return tool
