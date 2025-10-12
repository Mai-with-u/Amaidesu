"""
Log 版本的聊天动作实现

将聊天消息输出到日志，主要用于测试和调试。
"""

from __future__ import annotations  # ✅ 延迟类型注解求值

from src.utils.logger import get_logger
from ...interfaces import IChatAction
from ...interfaces.chat_action import ChatActionParams


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

    async def execute(self, params: ChatActionParams) -> bool:
        """
        执行聊天动作（输出到日志）。

        Args:
            params: ChatActionParams 类型，包含 message 字段

        Returns:
            执行是否成功
        """
        # ✅ validate_params 由 ValidatedAction 自动提供
        if not self.validate_params(params):
            self.logger.error(f"聊天动作参数验证失败: {params}")
            return False

        # ✅ IDE 现在知道 params["message"] 是 str 类型
        message = params["message"]

        # 额外的业务验证（可选）
        if not message.strip():
            self.logger.error("消息内容不能为空")
            return False

        if len(message) > 256:
            self.logger.error(f"消息长度超过限制 (256字符): {len(message)}")
            return False

        # 输出到日志
        self.logger.info(f"[MAICRAFT-CHAT] 发送聊天消息: '{message}'")

        return True
