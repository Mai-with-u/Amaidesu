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

        # 从配置加载命令映射
        self.command_map = self.config.get(
            "command_map",
            {
                "vts_trigger_hotkey": {"service": "vts_control", "method": "trigger_hotkey"},
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
        解析单个命令字符串并返回一个可执行的协程。
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
        service_name = command_config.get("service")
        method_name = command_config.get("method")

        if not service_name or not method_name:
            self.logger.error(f"指令 '{command_name}' 的配置不完整 (缺少 service 或 method)。")
            return None

        service_instance = self.core.get_service(service_name)
        if not service_instance:
            self.logger.warning(f"未找到指令 '{command_name}' 所需的服务 '{service_name}'。")
            return None

        method_to_call = getattr(service_instance, method_name, None)
        if not method_to_call or not asyncio.iscoroutinefunction(method_to_call):
            self.logger.warning(
                f"服务 '{service_name}' 上的方法 '{method_name}' 不存在或是非异步方法。无法执行指令 '{command_name}'。"
            )
            return None

        args = [arg.strip() for arg in args_str.split(",") if arg.strip()]

        # 返回协程本身，而不是在此处 await 它
        return method_to_call(*args)

    # on_connect 和 on_disconnect 可以在需要时实现
    # async def on_connect(self) -> None:
    #     self.logger.info("CommandProcessorPipeline 已连接")

    # async def on_disconnect(self) -> None:
    #     self.logger.info("CommandProcessorPipeline 已断开")
