"""
Debug Console Handler

调试用控制台输出 Handler，用于打印 Intent 内容到控制台。
"""

from typing import TYPE_CHECKING, Any, Dict, Literal

from pydantic import Field

from src.stages.output.registry import handler
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.types import Intent


@handler("debug_console")
class DebugConsoleHandler:
    """
    调试用控制台输出Handler

    直接打印 Intent 内容到控制台，用于调试和开发。
    """

    class ConfigSchema(BaseConfig):
        """调试控制台输出Handler配置"""

        type: Literal["debug_console"] = "debug_console"
        print_source_context: bool = Field(default=True, description="是否打印源上下文")
        print_actions: bool = Field(default=True, description="是否打印动作列表")
        print_metadata: bool = Field(default=False, description="是否打印元数据")
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

        # 打印基本信息
        print(f"ID:       {intent.id}")
        print(f"Timestamp:{intent.timestamp}")

        # 打印原始文本和回复文本
        print("\n[Text]")
        print(f"  Original:  {intent.original_text}")
        print(f"  Response:  {intent.response_text}")

        # 打印情感
        print("\n[Emotion]")
        print(f"  Type: {intent.emotion}")

        # 打印源上下文
        if self.print_source_context and intent.source_context:
            sc = intent.source_context
            print("\n[Source Context]")
            print(f"  Source:       {sc.source}")
            print(f"  Data Type:    {sc.data_type}")
            print(f"  User ID:      {sc.user_id or 'N/A'}")
            print(f"  User Nickname:{sc.user_nickname or 'N/A'}")
            print(f"  Importance:   {sc.importance}")

        # 打印动作列表
        if self.print_actions and intent.actions:
            print(f"\n[Actions] ({len(intent.actions)} total)")
            for i, action in enumerate(intent.actions, 1):
                print(f"  {i}. Type: {action.type}")
                print(f"     Priority: {action.priority}")
                if action.params:
                    print(f"     Params: {action.params}")

        # 打印元数据
        if self.print_metadata and intent.metadata:
            print("\n[Metadata]")
            for key, value in intent.metadata.items():
                print(f"  {key}: {value}")

        print(f"{'=' * 60}\n")

    async def cleanup(self) -> None:
        """清理 Handler"""
        self.logger.info("DebugConsoleHandler 清理完成")


__all__ = ["DebugConsoleHandler"]
