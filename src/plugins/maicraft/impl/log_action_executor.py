from typing import Dict, Any
from .action_executor_interface import ActionExecutor
from src.utils.logger import get_logger


class LogActionExecutor(ActionExecutor):
    """
    日志行动执行器，将所有行动输出到日志中。
    主要用于测试和调试目的。
    """

    def __init__(self):
        self.logger = get_logger("LogActionExecutor")
        self.initialized = False

    async def initialize(self) -> bool:
        """初始化日志执行器"""
        try:
            self.logger.info("日志行动执行器初始化完成")
            self.initialized = True
            return True
        except Exception as e:
            self.logger.error(f"日志执行器初始化失败: {e}")
            return False

    async def execute_action(self, action_id: str, params: Dict[str, Any]) -> bool:
        """
        执行行动，输出到日志。

        Args:
            action_id: 行动标识符
            params: 行动参数

        Returns:
            总是返回True（日志输出不会失败）
        """
        if not self.initialized:
            self.logger.warning("执行器未初始化，尝试自动初始化")
            await self.initialize()

        try:
            # 格式化参数字符串
            params_str = self._format_params(params)

            # 输出行动执行日志
            self.logger.info(f"[MAICRAFT] 执行行动: {action_id}, 参数: {params_str}")

            # 根据不同的行动类型输出更详细的信息
            if action_id == "minecraft_chat":
                message = params.get("message", "")
                self.logger.info(f"[MAICRAFT-CHAT] 发送聊天消息: '{message}'")
            else:
                self.logger.info(f"[MAICRAFT-{action_id.upper()}] 执行行动，参数: {params_str}")

            return True

        except Exception as e:
            self.logger.error(f"执行行动时出错: {action_id}, 参数: {params}, 错误: {e}")
            return False

    def _format_params(self, params: Dict[str, Any]) -> str:
        """
        格式化参数字典为可读字符串。

        Args:
            params: 参数字典

        Returns:
            格式化后的字符串
        """
        if not params:
            return "{}"

        try:
            # 简单的参数格式化，避免过长的输出
            formatted_items = []
            for key, value in params.items():
                if isinstance(value, str) and len(value) > 50:
                    # 截断过长的字符串
                    formatted_value = f"'{value[:47]}...'"
                elif isinstance(value, str):
                    formatted_value = f"'{value}'"
                else:
                    formatted_value = str(value)
                formatted_items.append(f"{key}: {formatted_value}")

            return "{" + ", ".join(formatted_items) + "}"

        except Exception as e:
            self.logger.warning(f"格式化参数时出错: {e}")
            return str(params)

    async def cleanup(self):
        """清理资源"""
        self.logger.info("日志行动执行器清理完成")
        self.initialized = False

    def get_executor_type(self) -> str:
        """获取执行器类型"""
        return "log"
