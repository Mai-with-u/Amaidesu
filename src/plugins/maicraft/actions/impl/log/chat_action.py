"""
Log 版本的聊天动作实现

将聊天消息输出到日志，主要用于测试和调试。
"""

from typing import Dict, Any
from src.utils.logger import get_logger
from ...action_interfaces import IChatAction


class LogChatAction(IChatAction):
    """
    Log 版本的聊天动作实现。
    将聊天消息输出到日志。
    """

    def __init__(self):
        self.logger = get_logger("LogChatAction")

    def get_action_type(self) -> str:
        """获取动作类型"""
        return "chat"

    async def execute(self, params: Dict[str, Any]) -> bool:
        """
        执行聊天动作（输出到日志）。

        Args:
            params: 必须包含 'message' 键

        Returns:
            执行是否成功
        """
        if not self.validate_params(params):
            self.logger.error(f"聊天动作参数验证失败: {params}")
            return False

        message = params.get("message", "")
        
        # 输出到日志
        self.logger.info(f"[MAICRAFT-CHAT] 发送聊天消息: '{message}'")
        
        return True

    def validate_params(self, params: Dict[str, Any]) -> bool:
        """
        验证聊天动作的参数。

        Args:
            params: 参数字典

        Returns:
            参数是否有效
        """
        # 检查必需参数
        if "message" not in params:
            self.logger.error("缺少必需参数: message")
            return False

        message = params.get("message", "")
        
        # 检查消息类型
        if not isinstance(message, str):
            self.logger.error(f"message 参数必须是字符串类型: {type(message)}")
            return False

        # 检查消息是否为空
        if not message.strip():
            self.logger.error("消息内容不能为空")
            return False

        # 检查消息长度（Minecraft 聊天消息长度限制）
        if len(message) > 256:
            self.logger.error(f"消息长度超过限制 (256字符): {len(message)}")
            return False

        return True

