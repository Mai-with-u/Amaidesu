"""
Debug Console Handler

调试用控制台输出 Handler，用于打印 Intent 内容到控制台。
"""

from typing import TYPE_CHECKING, Any, Dict

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.time_utils import ms_to_datetime
from src.stages.output.registry import handler

if TYPE_CHECKING:
    from src.modules.types import Intent


@handler("debug_console", label="调试控制台", description="调试信息输出控制台")
class DebugConsoleHandler:
    """
    调试用控制台输出Handler

    直接打印 Intent 内容到控制台，用于调试和开发。
    """

    class ConfigSchema(BaseConfig):
        """调试控制台输出Handler配置"""

        print_source_context: bool = Field(
            default=True, description="是否打印源上下文(来源 source_id / source_message_id)"
        )
        print_actions: bool = Field(default=True, description="是否打印动作(action)")
        print_metadata: bool = Field(default=False, description="是否打印元数据(metadata 全部字段)")
        prefix: str = Field(default="[DEBUG]", description="打印前缀")

    def __init__(self, config: Dict[str, Any], event_bus: EventBus):
        """
        初始化调试控制台Handler

        Args:
            config: Handler配置字典
            event_bus: EventBus实例
        """
        self.config = config
        self.event_bus = event_bus
        self.logger = get_logger("DebugConsoleHandler")

        # 使用 ConfigSchema 验证配置
        self.typed_config = self.ConfigSchema.from_dict(config)

        # 配置选项
        self.print_source_context = self.typed_config.print_source_context
        self.print_actions = self.typed_config.print_actions
        self.print_metadata = self.typed_config.print_metadata
        self.prefix = self.typed_config.prefix

        self.logger.info("DebugConsoleHandler 初始化完成")

    async def init(self) -> None:
        """初始化 Handler"""
        self.logger.info("DebugConsoleHandler 启动完成")

    async def handle(self, intent: "Intent") -> None:
        """
        执行意图 - 打印 Intent 内容到控制台

        Args:
            intent: 决策意图
        """
        # 打印分隔线
        print(f"\n{'=' * 60}")
        print(f"{self.prefix} Debug Console Output - Intent Received")
        print(f"{'=' * 60}")

        # 打印 Speech(可空)
        print("\n[Speech]")
        if intent.speech is not None:
            print(f"  {intent.speech}")
        else:
            print("  (no speech)")

        # 打印 Intent 标识
        print("\n[Identity]")
        print(f"  Intent ID:  {intent.metadata.intent_id}")
        print(f"  Source ID:  {intent.metadata.source_id}")
        decision_dt = ms_to_datetime(intent.metadata.decision_time_ms)
        print(f"  Decision:   {decision_dt} ({intent.metadata.decision_time_ms} ms)")

        # 打印源上下文(对应 source_id / source_message_id)
        if self.print_source_context:
            print("\n[Source Context]")
            print(f"  Source ID:       {intent.metadata.source_id}")
            print(f"  Source Msg ID:   {intent.metadata.source_message_id or 'N/A'}")

        # 打印情感(可选)
        if intent.emotion is not None:
            print("\n[Emotion]")
            print(f"  Name:      {intent.emotion.name}")
            print(f"  Intensity: {intent.emotion.intensity}")

        # 打印动作(可选)
        if self.print_actions and intent.action is not None:
            print("\n[Action]")
            print(f"  Name:       {intent.action.name}")
            if intent.action.parameters:
                print(f"  Parameters: {intent.action.parameters}")
            else:
                print("  Parameters: (none)")

        # 打印元数据(展开 IntentMetadata 4 个字段,逐字段打印)
        if self.print_metadata:
            print("\n[Metadata]")
            print(f"  source_id:         {intent.metadata.source_id}")
            print(f"  decision_time_ms:  {intent.metadata.decision_time_ms}")
            print(f"  source_message_id: {intent.metadata.source_message_id or 'N/A'}")
            print(f"  intent_id:         {intent.metadata.intent_id}")

        print(f"{'=' * 60}\n")

    async def cleanup(self) -> None:
        """清理 Handler"""
        self.logger.info("DebugConsoleHandler 清理完成")


__all__ = ["DebugConsoleHandler"]
