from typing import Dict, Any, TYPE_CHECKING
from .base_action import BaseAction
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from ..impl.action_executor_interface import ActionExecutor


class ChatAction(BaseAction):
    """
    聊天行动，用于在游戏中发送聊天消息。
    支持多种命令名称映射到同一个行动（如 chat、say、whisper）。
    """

    def __init__(self):
        self.logger = get_logger("ChatAction")

    def get_action_id(self) -> str:
        """获取行动标识（独立于命令名称）"""
        return "minecraft_chat"

    async def execute(self, params: Dict[str, Any], executor: "ActionExecutor") -> bool:
        """
        执行聊天行动。

        Args:
            params: 行动参数，应包含 'message' 键
            executor: 行动执行器实例

        Returns:
            执行是否成功
        """
        # 验证参数
        if not self.validate_params(params):
            self.logger.error(f"聊天行动参数验证失败: {params}")
            return False

        message = params.get("message", "").strip()
        if not message:
            self.logger.warning("聊天消息为空，跳过执行")
            return False

        # 调用执行器执行具体的聊天行动
        try:
            success = await executor.execute_action(self.get_action_id(), {"message": message})
            if success:
                self.logger.debug(f"聊天行动执行成功: '{message}'")
            else:
                self.logger.warning(f"聊天行动执行失败: '{message}'")
            return success
        except Exception as e:
            self.logger.error(f"执行聊天行动时出错: {e}", exc_info=True)
            return False

    def validate_params(self, params: Dict[str, Any]) -> bool:
        """
        验证聊天行动的参数。

        Args:
            params: 参数字典

        Returns:
            参数是否有效
        """
        # 检查必需参数
        if not self.validate_required_params(params):
            return False

        # 检查消息内容
        message = params.get("message", "")
        if not isinstance(message, str):
            self.logger.error(f"消息参数必须是字符串类型: {type(message)}")
            return False

        if not message.strip():
            self.logger.error("消息内容不能为空")
            return False

        # 可以添加更多验证逻辑，比如消息长度限制
        if len(message) > 256:  # Minecraft聊天消息长度限制
            self.logger.error(f"消息长度超过限制 (256字符): {len(message)}")
            return False

        return True

    def get_required_params(self) -> list[str]:
        """获取必需的参数列表"""
        return ["message"]
