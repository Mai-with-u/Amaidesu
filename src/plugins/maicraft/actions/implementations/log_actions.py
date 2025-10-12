"""
Log 系列动作实现

这些动作实现仅将动作内容输出到日志，主要用于测试和调试。
"""

from typing import Dict, Any
from src.utils.logger import get_logger
from ..action_interfaces import IChatAction, IAttackAction


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


class LogAttackAction(IAttackAction):
    """
    Log 版本的攻击动作实现。
    将攻击目标输出到日志。
    """

    def __init__(self):
        self.logger = get_logger("LogAttackAction")

    def get_action_type(self) -> str:
        """获取动作类型"""
        return "attack"

    async def execute(self, params: Dict[str, Any]) -> bool:
        """
        执行攻击动作（输出到日志）。

        Args:
            params: 必须包含 'mob_name' 键

        Returns:
            执行是否成功
        """
        if not self.validate_params(params):
            self.logger.error(f"攻击动作参数验证失败: {params}")
            return False

        mob_name = params.get("mob_name", "")

        # 输出到日志
        self.logger.info(f"[MAICRAFT-ATTACK] 攻击生物: '{mob_name}'")

        return True

    def validate_params(self, params: Dict[str, Any]) -> bool:
        """
        验证攻击动作的参数。

        Args:
            params: 参数字典

        Returns:
            参数是否有效
        """
        # 检查必需参数
        if "mob_name" not in params:
            self.logger.error("缺少必需参数: mob_name")
            return False

        mob_name = params.get("mob_name", "")

        # 检查生物名称类型
        if not isinstance(mob_name, str):
            self.logger.error(f"mob_name 参数必须是字符串类型: {type(mob_name)}")
            return False

        # 检查生物名称是否为空
        if not mob_name.strip():
            self.logger.error("生物名称不能为空")
            return False

        # 检查生物名称长度
        if len(mob_name) > 100:
            self.logger.error(f"生物名称过长 (最多100字符): {len(mob_name)}")
            return False

        return True
