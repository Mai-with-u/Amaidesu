from typing import Dict, Any, Optional
import time
from maim_message import MessageBase
from src.core.pipeline_manager import MessagePipeline


class CommandRouterPipeline(MessagePipeline):
    """
    命令路由管道，检测命令消息并发布事件。

    功能：
    1. 检测消息是否以指定前缀开头（默认以"/"开头）
    2. 发布命令事件供插件监听
    3. 兼容旧的服务调用方式（向后兼容）
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self.enabled = self.config.get("enabled", True)
        self.command_prefix = self.config.get("command_prefix", "/")
        self.subscribers = self.config.get("subscribers", [])  # 保留用于向后兼容
        self.use_events = self.config.get("use_events", True)  # 默认使用事件系统

        self.logger.info(f"命令路由管道初始化完成，前缀: '{self.command_prefix}', 使用事件: {self.use_events}")
        if self.subscribers and not self.use_events:
            self.logger.info(f"向后兼容模式，订阅插件: {self.subscribers}")

    async def process_message(self, message: MessageBase) -> Optional[MessageBase]:
        """
        处理消息，检测命令前缀并发布事件。

        Args:
            message: 要处理的消息对象

        Returns:
            处理后的消息对象，如果消息被完全处理则返回None
        """
        if not self.enabled:
            return message

        # 只处理文本类型的消息段
        if (
            not message.message_segment
            or message.message_segment.type != "text"
            or not isinstance(message.message_segment.data, str)
        ):
            return message

        original_text = message.message_segment.data.strip()
        if not original_text:
            return message

        # 检查是否以命令前缀开头
        if not original_text.startswith(self.command_prefix):
            return message

        # 记录原始消息正文
        self.logger.info(f"检测到命令消息: '{original_text}'")

        # 准备命令事件数据
        command_data = {
            "command": original_text,
            "prefix": self.command_prefix,
            "message": message,  # 包含完整消息对象
            "user_id": message.message_info.user_info.user_id if message.message_info.user_info else None,
            "username": message.message_info.user_info.user_nickname if message.message_info.user_info else None,
            "timestamp": time.time(),
        }

        # 使用事件系统发布命令
        if self.use_events and hasattr(self, "core") and self.core:
            try:
                event_bus = self.core.event_bus
                if event_bus:
                    # 使用事件系统发布命令事件
                    self.logger.info(f"发布命令事件: {original_text}")
                    await event_bus.emit("command_router.received", command_data, source="CommandRouter")
                else:
                    self.logger.warning("EventBus 不可用，命令将被忽略")
            except (AttributeError, TypeError) as e:
                self.logger.error(f"发布命令事件失败: {e}，命令将被忽略", exc_info=True)
        else:
            self.logger.warning("未启用事件系统或未连接到 core，命令将被忽略")

        # 命令消息被处理，返回None
        self.logger.info("命令消息处理完成")
        return None
