import re
from typing import Dict, Any, Optional, List
from maim_message import MessageBase
from src.core.pipeline_manager import MessagePipeline


class CommandRouterPipeline(MessagePipeline):
    """
    命令路由管道，拦截包含指定前缀的命令消息，转发给订阅的插件列表。

    功能：
    1. 检测消息中的命令（默认以"/"开头）
    2. 将命令转发给配置的订阅插件
    3. 从原消息中移除已处理的命令
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self.enabled = self.config.get("enabled", True)
        self.command_prefix = self.config.get("command_prefix", "/")
        self.subscribers = self.config.get("subscribers", [])

        # 编译命令匹配正则表达式
        # 匹配以指定前缀开头的命令，支持参数
        escaped_prefix = re.escape(self.command_prefix)
        self.command_pattern = re.compile(rf"^{escaped_prefix}(\w+)(?:\s+(.*))?$", re.MULTILINE)

        self.logger.info(f"命令路由管道初始化完成，前缀: '{self.command_prefix}', 订阅插件: {self.subscribers}")

    async def process_message(self, message: MessageBase) -> Optional[MessageBase]:
        """
        处理消息，检测命令并转发给订阅插件。

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

        # 检查是否包含命令
        commands_found = self.command_pattern.findall(original_text)
        if not commands_found:
            return message

        self.logger.info(f"在消息中发现 {len(commands_found)} 个命令: {[cmd[0] for cmd in commands_found]}")

        # 转发命令给订阅的插件
        for command_name, command_args in commands_found:
            await self._forward_command_to_subscribers(message, command_name, command_args)

        # 从原消息中移除命令
        processed_text = self.command_pattern.sub("", original_text).strip()

        # 如果处理后文本为空，返回None表示消息被完全处理
        if not processed_text:
            self.logger.debug("消息中的所有内容都是命令，消息被完全处理")
            return None

        # 更新消息内容
        if processed_text != original_text:
            self.logger.debug(f"原始文本: '{original_text}' -> 处理后文本: '{processed_text}'")
            message.message_segment.data = processed_text

        return message

    async def _forward_command_to_subscribers(
        self, original_message: MessageBase, command_name: str, command_args: str
    ):
        """
        将命令转发给订阅的插件。

        Args:
            original_message: 原始消息对象
            command_name: 命令名称
            command_args: 命令参数字符串
        """
        if not self.subscribers:
            self.logger.warning(f"没有配置订阅插件，命令 '{command_name}' 将被忽略")
            return

        # 构造完整的命令文本
        command_text = f"/{command_name}"
        if command_args.strip():
            command_text += f" {command_args}"

        for subscriber in self.subscribers:
            try:
                self.logger.debug(f"转发命令 '{command_name}' 给插件 '{subscriber}'")
                await self._notify_subscriber(subscriber, command_text, original_message)
            except Exception as e:
                self.logger.error(f"转发命令给插件 '{subscriber}' 时出错: {e}", exc_info=True)

    async def _notify_subscriber(self, subscriber_name: str, command_text: str, original_message: MessageBase):
        """
        通知订阅插件处理命令。

        Args:
            subscriber_name: 订阅插件名称
            command_text: 完整的命令文本
            original_message: 原始消息对象
        """
        if not hasattr(self, "core") or self.core is None:
            self.logger.warning("无法转发命令：管道未设置core引用")
            return

        # 通过服务注册机制转发命令
        service_name = f"{subscriber_name}_command_handler"
        if command_handler := self.core.get_service(service_name):
            try:
                # 调用插件的命令处理方法
                success = await command_handler(command_text, original_message)
                if success:
                    self.logger.info(f"[转发成功] 插件: {subscriber_name}, 命令: '{command_text}'")
                else:
                    self.logger.warning(f"[转发失败] 插件: {subscriber_name}, 命令: '{command_text}'")
            except Exception as e:
                self.logger.error(
                    f"转发命令时出错: 插件={subscriber_name}, 命令='{command_text}', 错误={e}", exc_info=True
                )
        else:
            self.logger.warning(f"未找到插件 '{subscriber_name}' 的命令处理服务 '{service_name}'")
