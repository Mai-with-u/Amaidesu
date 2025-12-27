"""
LLM 工具调用执行器

解析并执行 LLM 返回的工具调用，分发到对应的适配器方法。
"""

from typing import Dict, Any, Optional

from src.utils.logger import get_logger


class LLMExecutor:
    """LLM 工具调用执行器

    解析 LLM 返回的工具调用指令，并调用相应的适配器方法执行。
    """

    def __init__(self, manager):
        """
        初始化执行器

        Args:
            manager: AvatarControlManager 实例
        """
        self.manager = manager
        self.logger = get_logger("LLMExecutor")

    async def execute(
        self,
        function_name: str,
        arguments: Dict[str, Any],
        adapter_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """执行工具调用

        Args:
            function_name: 函数/工具名称
            arguments: 函数参数字典
            adapter_name: 目标适配器名称（None 则使用活跃适配器）

        Returns:
            执行结果 {"success": bool, "message": str, ...}
        """
        try:
            if function_name == "set_avatar_expression":
                return await self._execute_expression(arguments, adapter_name)
            elif function_name == "set_avatar_parameters":
                return await self._execute_parameters(arguments, adapter_name)
            elif function_name == "trigger_avatar_action":
                return await self._execute_action(arguments, adapter_name)
            else:
                return {
                    "success": False,
                    "error": f"未知的函数: {function_name}"
                }
        except Exception as e:
            self.logger.error(f"执行工具调用失败: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def _execute_expression(
        self,
        arguments: Dict[str, Any],
        adapter_name: Optional[str]
    ) -> Dict[str, Any]:
        """执行表情设置（语义动作）

        Args:
            arguments: 包含 expression 和 intensity 的参数字典
            adapter_name: 目标适配器名称

        Returns:
            执行结果
        """
        expression = arguments.get("expression")
        intensity = arguments.get("intensity", 1.0)

        if not expression:
            return {
                "success": False,
                "error": "缺少必需参数: expression"
            }

        # 验证强度范围
        if not 0.0 <= intensity <= 1.0:
            return {
                "success": False,
                "error": f"intensity 必须在 0.0-1.0 范围内，当前值: {intensity}"
            }

        # 检查语义动作是否存在
        if not self.manager.semantic_mapper.has_action(expression):
            self.logger.warning(f"未知的语义动作: {expression}")
            return {
                "success": False,
                "error": f"未知的语义动作: {expression}"
            }

        # 获取目标适配器
        adapter = self._get_target_adapter(adapter_name)
        if not adapter:
            return {
                "success": False,
                "error": f"未找到适配器: {adapter_name or 'active'}"
            }

        # 获取语义动作映射
        mapping = self.manager.semantic_mapper.get_mapping(
            expression,
            adapter.adapter_name
        )

        if not mapping:
            return {
                "success": False,
                "error": f"语义动作 '{expression}' 没有针对平台 '{adapter.adapter_name}' 的映射"
            }

        # 应用强度系数
        adjusted_mapping = self.manager.semantic_mapper.apply_intensity(
            mapping,
            intensity
        )

        # 执行参数设置
        success = await adapter.set_parameters(adjusted_mapping)

        return {
            "success": success,
            "message": f"表情 '{expression}' 设置成功" if success else "设置失败",
            "expression": expression,
            "intensity": intensity,
            "parameters_set": adjusted_mapping if success else {}
        }

    async def _execute_parameters(
        self,
        arguments: Dict[str, Any],
        adapter_name: Optional[str]
    ) -> Dict[str, Any]:
        """执行参数设置（直接参数控制）

        Args:
            arguments: 参数名到值的映射
            adapter_name: 目标适配器名称

        Returns:
            执行结果
        """
        # 获取目标适配器
        adapter = self._get_target_adapter(adapter_name)
        if not adapter:
            return {
                "success": False,
                "error": f"未找到适配器: {adapter_name or 'active'}"
            }

        # 过滤掉 None 值
        parameters = {
            k: v for k, v in arguments.items()
            if v is not None
        }

        if not parameters:
            return {
                "success": False,
                "error": "没有要设置的参数"
            }

        # 执行参数设置
        success = await adapter.set_parameters(parameters)

        return {
            "success": success,
            "message": f"设置了 {len(parameters)} 个参数" if success else "设置失败",
            "parameters_set": parameters if success else {}
        }

    async def _execute_action(
        self,
        arguments: Dict[str, Any],
        adapter_name: Optional[str]
    ) -> Dict[str, Any]:
        """执行动作触发

        Args:
            arguments: 动作名到布尔值的映射
            adapter_name: 目标适配器名称

        Returns:
            执行结果
        """
        # 获取目标适配器
        adapter = self._get_target_adapter(adapter_name)
        if not adapter:
            return {
                "success": False,
                "error": f"未找到适配器: {adapter_name or 'active'}"
            }

        # 找到设置为 true 的动作
        triggered_actions = [
            action_name for action_name, value in arguments.items()
            if value is True
        ]

        if not triggered_actions:
            return {
                "success": False,
                "error": "没有触发的动作"
            }

        # 触发第一个动作（通常一次只触发一个）
        action_name = triggered_actions[0]
        success = await adapter.trigger_action(action_name)

        return {
            "success": success,
            "message": f"动作 '{action_name}' 触发成功" if success else "触发失败",
            "action_triggered": action_name
        }

    def _get_target_adapter(self, adapter_name: Optional[str]):
        """获取目标适配器

        Args:
            adapter_name: 适配器名称，None 则使用活跃适配器

        Returns:
            适配器实例，如果未找到返回 None
        """
        if adapter_name:
            return self.manager.get_adapter(adapter_name)
        return self.manager.get_active_adapter()
