# src/pipelines/command_processor/pipeline.py
import asyncio
import re
from typing import Dict, Any, Optional

from src.core.pipeline_manager import MessagePipeline
from maim_message import MessageBase


class CommandProcessorPipeline(MessagePipeline):
    """
    一个入站管道，用于处理从 MaiCore 返回的消息。
    它会查找消息中的嵌入式命令，执行它们，然后从文本中移除这些命令标记，
    以便下游插件（如TTS）可以接收到干净的文本。
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.enabled = self.config.get("enabled", True)

        if not self.enabled:
            self.logger.warning("CommandProcessorPipeline 在配置中被禁用。")
            return

        self.command_pattern_str = self.config.get("command_pattern", r"%\\{([^%{}]+)\\}")
        try:
            self.command_pattern = re.compile(self.command_pattern_str)
            self.logger.info(f"使用指令匹配模式: {self.command_pattern_str}")
        except re.error as e:
            self.logger.error(f"无效的指令匹配模式 '{self.command_pattern_str}': {e}。管道已禁用。")
            self.enabled = False
            self.command_pattern = None

        # 从配置加载命令映射（新版：事件发布模式）
        # 配置格式: {"command_name": {"event": "event_name", "event_key": "data_key"}}
        self.command_map = self.config.get(
            "command_map",
            {
                "vts_trigger_hotkey": {"event": "vts.trigger_hotkey", "event_key": "hotkey_name"},
            },
        )
        self.logger.debug(f"使用命令映射初始化: {self.command_map}")

    async def process_message(self, message: MessageBase) -> Optional[MessageBase]:
        """
        处理消息，查找、执行并移除命令标签。
        """
        if (
            not self.enabled
            or not self.command_pattern
            or not message.message_segment
            or message.message_segment.type != "text"
        ):
            return message

        original_text = message.message_segment.data
        if not isinstance(original_text, str):
            return message

        commands_found = self.command_pattern.findall(original_text)
        if not commands_found:
            return message

        self.logger.info(f"在消息 {message.message_info.message_id} 中找到 {len(commands_found)} 个指令。")

        # 异步执行所有找到的命令
        coroutines_to_run = []
        for command_full_match in commands_found:
            coro = self._execute_single_command(command_full_match)
            if coro:
                coroutines_to_run.append(coro)

        if coroutines_to_run:
            # "即发即忘" (Fire-and-forget) 模式：
            # 我们将所有命令的协程收集起来，然后使用 asyncio.create_task()
            # 来安排它们的并发执行，但我们不在此处等待它们完成 (不使用 await)。
            # 这允许消息处理流程（例如，将清理后的文本发送到TTS）可以无阻塞地继续进行。
            for coro in coroutines_to_run:
                asyncio.create_task(coro)

        # 从文本中移除所有命令标签
        processed_text = self.command_pattern.sub("", original_text).strip()

        if processed_text != original_text:
            self.logger.debug(f"原始文本: '{original_text}'")
            self.logger.info(f"处理后文本 (指令已移除): '{processed_text}'")
            message.message_segment.data = processed_text

        return message

    async def _execute_single_command(self, command_full_match: str) -> Optional[asyncio.Task]:
        """
        解析单个命令字符串并发布事件。
        返回 None 如果命令无效或无法执行。
        """
        self.logger.debug(f"处理指令标签内容: '{command_full_match}'")

        parts = command_full_match.strip().split(":", 1)
        command_name = parts[0]
        args_str = parts[1] if len(parts) > 1 else ""

        if command_name not in self.command_map:
            self.logger.warning(f"发现未知指令: '{command_name}'")
            return None

        command_config = self.command_map[command_name]
        event_name = command_config.get("event")
        event_key = command_config.get("event_key", "args")

        if not event_name:
            self.logger.error(f"指令 '{command_name}' 的配置不完整 (缺少 event)。")
            return None

        # 检查 EventBus 是否可用
        if not hasattr(self, "core") or not self.core or not self.core.event_bus:
            self.logger.warning(f"EventBus 不可用，无法执行指令 '{command_name}'")
            return None

        # 准备事件数据
        args = [arg.strip() for arg in args_str.split(",") if arg.strip()]
        event_data = {event_key: args[0] if len(args) == 1 else args}

        # 发布事件（fire-and-forget）
        async def emit_event():
            try:
                await self.core.event_bus.emit(
                    f"command_processor.{event_name}",
                    event_data,
                    source="CommandProcessor"
                )
                self.logger.info(f"指令 '{command_name}' 已发布到事件 '{event_name}'")
            except Exception as e:
                self.logger.error(f"发布指令事件失败: {e}", exc_info=True)

        return emit_event()

    # on_connect 和 on_disconnect 可以在需要时实现
    # async def on_connect(self) -> None:
    #     self.logger.info("CommandProcessorPipeline 已连接")

    # async def on_disconnect(self) -> None:
    #     self.logger.info("CommandProcessorPipeline 已断开")
