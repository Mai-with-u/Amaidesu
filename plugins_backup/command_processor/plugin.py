# Command Processor Plugin - 新Plugin架构实现

import re
from typing import Dict, Any, List

from src.utils.logger import get_logger
from maim_message import MessageBase


class CommandProcessorPlugin:
    """
    Command Processor插件 - 处理消息中的命令标签

    功能：
    - 拦截来自MaiCore的消息
    - 识别并执行嵌入的命令（如 %{vts_trigger_hotkey}%）
    - 通过EventBus发布命令执行事件
    - 从消息文本中移除命令标签

    迁移到新的Plugin架构，通过EventBus进行通信。
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        self.event_bus = None

        # 配置检查
        self.enabled = self.config.get("enabled", True)
        if not self.enabled:
            self.logger.warning("CommandProcessorPlugin 在配置中已禁用。")
            return

        # 命令模式
        # 匹配格式: %{command_name}%
        # 例如: %{vts_trigger_hotkey}%
        self.command_pattern_str = self.config.get("command_pattern", r"%{([^}]+)}%")
        try:
            self.command_pattern = re.compile(self.command_pattern_str)
            self.logger.info(f"使用命令匹配模式: {self.command_pattern_str}")
        except re.error as e:
            self.logger.error(f"无效的命令匹配模式 '{self.command_pattern_str}': {e}。插件已禁用。")
            self.enabled = False
            self.command_pattern = None

        # 命令映射（硬编码，可后续改为配置化）
        self.command_map = {
            "vts_trigger_hotkey": {"event": "vts.trigger_hotkey"},
            # 在此处添加更多命令
        }
        self.logger.debug(f"使用命令映射初始化: {self.command_map}")

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表（空列表，因为这是处理插件）
        """
        self.event_bus = event_bus

        if not self.enabled:
            return []

        # 订阅消息事件（监听所有消息）
        # 注意：这里订阅的是系统级事件，不是WebSocket消息
        # 需要确定具体的事件名称
        event_bus.on("message.received", self._handle_message, priority=100)

        self.logger.info("CommandProcessorPlugin 已订阅 message.received 事件")

        return []

    async def _handle_message(self, event_name: str, data: Dict[str, Any], source: str):
        """
        处理消息事件

        Args:
            event_name: 事件名称
            data: 事件数据（包含message）
            source: 事件来源
        """
        if not self.enabled or not self.command_pattern:
            return

        # 获取MessageBase对象
        message = data.get("message")
        if not isinstance(message, MessageBase):
            return

        # 仅处理文本消息
        if not message.message_segment or message.message_segment.type != "text":
            return

        original_text = message.message_segment.data
        if not isinstance(original_text, str):
            self.logger.warning(f"文本段预期为字符串数据，但得到 {type(original_text)}。跳过命令处理。")
            return

        processed_text = original_text
        commands_found = self.command_pattern.findall(original_text)

        if not commands_found:
            return  # 未找到命令，无需执行任何操作

        self.logger.info(f"在消息 {message.message_info.message_id} 中找到 {len(commands_found)} 个潜在指令。")

        # 执行命令
        for command_full_match in commands_found:
            # command_full_match 是 %{...}% 内部的内容
            self.logger.debug(f"处理命令标签内容: '{command_full_match}'")

            # 简单解析: command:arg1,arg2... (按第一个 ':' 分割)
            parts = command_full_match.strip().split(":", 1)
            command_name = parts[0]
            args_str = parts[1] if len(parts) > 1 else ""

            if command_name in self.command_map:
                command_config = self.command_map[command_name]
                event_to_emit = command_config["event"]

                # 基本参数解析 (按逗号分割，去除空白)
                args = [arg.strip() for arg in args_str.split(",") if arg.strip()]

                self.logger.info(f"通过事件 '{event_to_emit}' 执行命令 '{command_name}' (参数: {args})")

                # 发布命令执行事件
                await self.event_bus.emit(
                    event_to_emit, {"command": command_name, "args": args}, "CommandProcessorPlugin"
                )
            else:
                self.logger.warning(f"发现未知命令: '{command_name}'")

        # 从文本中移除所有命令标签
        processed_text = self.command_pattern.sub("", original_text).strip()

        # 直接修改消息段数据
        if processed_text != original_text:
            self.logger.debug(f"原始文本: '{original_text}'")
            self.logger.info(f"处理后文本 (命令已移除): '{processed_text}'")
            message.message_segment.data = processed_text

            # 发布消息已更新事件
            await self.event_bus.emit(
                "message.updated",
                {"message_id": message.message_info.message_id, "new_text": processed_text},
                "CommandProcessorPlugin",
            )

    async def cleanup(self):
        """清理资源"""
        self.logger.info("开始清理 CommandProcessorPlugin...")

        if self.event_bus:
            # 取消订阅事件
            self.event_bus.off("message.received", self._handle_message)
            self.logger.info("已取消订阅 message.received 事件")

        self.logger.info("CommandProcessorPlugin 清理完成")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "CommandProcessor",
            "version": "1.0.0",
            "author": "Amaidesu Team",
            "description": "命令处理器插件，处理消息中的命令标签并执行相应操作",
            "category": "processing",
            "api_version": "1.0",
        }


# 插件入口点
plugin_entrypoint = CommandProcessorPlugin
