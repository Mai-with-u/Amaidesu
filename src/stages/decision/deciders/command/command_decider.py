"""Command Decision Decider

通用命令意图路由器：将命令形式的标准化消息转换为 Intent。
"""

from typing import Optional

from pydantic import Field, ValidationError

from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import IntentPayload
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.types import Intent, IntentMetadata
from src.modules.types.base.normalized_message import NormalizedMessage
from src.stages.decision.registry import decider

from .command_parser import CommandParser
from .command_registry import CommandRegistry
from .config import CommandDeciderConfig


@decider("command")
class CommandDecider:
    """通用命令意图路由器。

    订阅 input.message.received，将命令形式（如 `/attack zombie`）的消息
    转换为带 structured_params["common"] 的 Intent。speech=None，TTS 不朗读。
    """

    class ConfigSchema(BaseConfig):
        command_mappings: dict = Field(
            default_factory=lambda: {
                "chat": "chat",
                "say": "chat",
                "聊天": "chat",
                "attack": "attack",
                "攻击": "attack",
            },
            description="命令映射：{命令名 → 动作字符串}",
        )
        command_prefix: str = Field(default="/", description="命令前缀")

    def __init__(self, config: dict, event_bus: EventBus):
        self.logger = get_logger(self.__class__.__name__)
        self._event_bus = event_bus

        try:
            self.parsed_config = CommandDeciderConfig(**config)
        except ValidationError as e:
            self.logger.error(f"配置验证失败: {e}")
            raise

        self.command_parser = CommandParser(command_prefix=self.parsed_config.command_prefix)
        self.command_registry = CommandRegistry()
        self._register_commands()

        self.logger.info("CommandDecider 初始化完成")

    def _register_commands(self) -> None:
        command_mappings = self.parsed_config.command_mappings
        if not command_mappings:
            self.logger.warning("未配置命令映射")
            return
        try:
            self.command_registry.load_from_config(command_mappings)
            supported = self.command_registry.get_supported_commands()
            self.logger.info(f"已注册 {len(supported)} 个命令: {supported}")
        except Exception as e:
            self.logger.error(f"注册命令映射失败: {e}", exc_info=True)
            raise

    async def setup(self) -> None:
        self.logger.info("CommandDecider 设置完成")

    async def cleanup(self) -> None:
        self.command_registry.clear()
        self.logger.info("CommandDecider 清理完成")

    async def decide(self, message: "NormalizedMessage") -> None:
        try:
            text = self._extract_message_text(message)
            if not text:
                return

            command = self.command_parser.parse_command(text, message)
            if not command:
                return

            self.logger.debug(f"收到命令: {command.name} (参数: {command.args})")

            if not self.command_registry.is_supported_command(command.name):
                self.logger.debug(f"不支持的命令: '{command.name}'")
                return

            action = self.command_registry.get_action(command.name)
            if not action:
                self.logger.error(f"无法获取命令的动作: '{command.name}'")
                return

            command_summary = f"/{command.name} {command.raw_args}".strip()
            intent = Intent(
                action=None,
                speech=command_summary,
                metadata=IntentMetadata(
                    source_id="command",
                    decision_time_ms=now_ms(),
                    source_message_id=message.message_id,
                ),
            )

            self.logger.info(f"生成命令 Intent: 命令='{command.name}' -> 动作='{action}' 参数={command.args}")
            await self._publish_intent(intent)

        except Exception as e:
            self.logger.error(f"处理命令时出错: {e}", exc_info=True)
            return

    def _extract_message_text(self, message: "NormalizedMessage") -> Optional[str]:
        try:
            if hasattr(message, "content") and message.content:
                return str(message.content).strip()
            if hasattr(message, "text") and message.text:
                return str(message.text).strip()
            return None
        except Exception as e:
            self.logger.debug(f"提取消息文本失败: {e}")
            return None

    async def _publish_intent(self, intent: Intent) -> None:
        if not self._event_bus:
            self.logger.error("EventBus 未初始化，无法发布事件")
            return
        await self._event_bus.emit(
            CoreEvents.DECISION_INTENT_GENERATED,
            IntentPayload.from_intent(intent, "command"),
            source="CommandDecider",
        )

    @classmethod
    def get_registration_info(cls) -> dict:
        return {
            "layer": "decision",
            "name": "command",
            "class": cls,
            "source": "builtin:command",
        }
