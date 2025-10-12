from typing import Dict, Any, Optional
from maim_message import MessageBase
from src.core.pipeline_manager import MessagePipeline


class CommandRouterPipeline(MessagePipeline):
    """
    命令路由管道，拦截包含指定前缀的命令消息，转发给订阅的插件列表。

    功能：
    1. 检测消息是否以指定前缀开头（默认以"/"开头）
    2. 将整个消息对象转发给配置的订阅插件自行处理
    3. 最多只记录原始消息的正文日志
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self.enabled = self.config.get("enabled", True)
        self.command_prefix = self.config.get("command_prefix", "/")
        self.subscribers = self.config.get("subscribers", [])

        self.logger.info(f"命令路由管道初始化完成，前缀: '{self.command_prefix}', 订阅插件: {self.subscribers}")

    async def process_message(self, message: MessageBase) -> Optional[MessageBase]:
        """
        处理消息，检测命令前缀并转发整个消息给订阅插件。

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
        self.logger.info(f"检测到命令消息，原始消息正文: '{original_text}'")

        # 转发整个消息给订阅的插件
        await self._forward_message_to_subscribers(message)

        # 发现命令直接拦截，返回None表示消息被完全处理
        self.logger.debug("命令消息被拦截，转发给订阅插件")
        return None

    async def _forward_message_to_subscribers(self, message: MessageBase):
        """
        将整个消息转发给订阅的插件。

        Args:
            message: 要转发的消息对象
        """
        if not self.subscribers:
            self.logger.warning("没有配置订阅插件，消息将被忽略")
            return

        for subscriber in self.subscribers:
            try:
                self.logger.debug(f"转发消息给插件 '{subscriber}'")
                await self._notify_subscriber(subscriber, message)
            except Exception as e:
                self.logger.error(f"转发消息给插件 '{subscriber}' 时出错: {e}", exc_info=True)

    async def _notify_subscriber(self, subscriber_name: str, message: MessageBase):
        """
        通知订阅插件处理消息。

        Args:
            subscriber_name: 订阅插件名称
            message: 要处理的消息对象
        """
        if not hasattr(self, "core") or self.core is None:
            self.logger.warning("无法转发消息：管道未设置core引用")
            return

        # 通过服务注册机制转发消息
        service_name = f"{subscriber_name}_command_handler"
        if command_handler := self.core.get_service(service_name):
            try:
                # 调用插件的消息处理方法
                success = await command_handler(message)
                if success:
                    self.logger.info(f"[转发成功] 插件: {subscriber_name}")
                else:
                    self.logger.warning(f"[转发失败] 插件: {subscriber_name}")
            except Exception as e:
                self.logger.error(f"转发消息时出错: 插件={subscriber_name}, 错误={e}", exc_info=True)
        else:
            self.logger.warning(f"未找到插件 '{subscriber_name}' 的命令处理服务 '{service_name}'")
