"""
Debug Console Output Provider

调试用控制台输出 Provider，用于打印 Intent 内容到控制台。
"""

from typing import TYPE_CHECKING, Any, Dict, Literal

from pydantic import Field

from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
from src.modules.registry import ProviderRegistry
from src.modules.types.base.output_provider import OutputProvider

if TYPE_CHECKING:
    from src.modules.di.context import ProviderContext
    from src.modules.types import Intent


class DebugConsoleOutputProvider(OutputProvider):
    """
    调试用控制台输出Provider

    直接打印 Intent 内容到控制台，用于调试和开发。
    """

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        """获取 Provider 注册信息"""
        return {"layer": "output", "name": "debug_console", "class": cls, "source": "builtin:debug_console"}

    class ConfigSchema(BaseProviderConfig):
        """调试控制台输出Provider配置"""

        type: Literal["debug_console"] = "debug_console"
        print_source_context: bool = Field(default=True, description="是否打印源上下文")
        print_actions: bool = Field(default=True, description="是否打印动作列表")
        print_metadata: bool = Field(default=False, description="是否打印元数据")
        prefix: str = Field(default="[DEBUG]", description="打印前缀")

    def __init__(self, config: dict, context: "ProviderContext"):
        super().__init__(config, context)
        self.logger = get_logger("DebugConsoleOutputProvider")

        # 使用 ConfigSchema 验证配置
        self.typed_config = self.ConfigSchema(**config)

        # 配置选项
        self.print_source_context = self.typed_config.print_source_context
        self.print_actions = self.typed_config.print_actions
        self.print_metadata = self.typed_config.print_metadata
        self.prefix = self.typed_config.prefix

        self.logger.info("DebugConsoleOutputProvider 初始化完成")

    async def init(self) -> None:
        """初始化 Provider"""
        self.logger.info("DebugConsoleOutputProvider 启动完成")

    async def execute(self, intent: "Intent") -> None:
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
        """清理 Provider"""
        self.logger.info("DebugConsoleOutputProvider 清理完成")


# 注册到 ProviderRegistry
ProviderRegistry.register_output("debug_console", DebugConsoleOutputProvider, source="builtin:debug_console")

__all__ = ["DebugConsoleOutputProvider"]
