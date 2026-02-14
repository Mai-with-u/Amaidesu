"""Mock Output Provider - 用于测试"""

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
from src.modules.registry import ProviderRegistry
from src.modules.types.base.output_provider import OutputProvider

if TYPE_CHECKING:
    from src.modules.di.context import ProviderContext
    from src.modules.types import Intent


class MockOutputProvider(OutputProvider):
    """
    模拟输出Provider（用于测试）

    记录收到的所有 Intent，不进行实际渲染。
    """

    class ConfigSchema(BaseProviderConfig):
        """模拟输出Provider配置（用于测试）"""

        type: Literal["mock"] = "mock"
        log_received: bool = Field(default=True, description="是否记录收到的参数")

    def __init__(self, config: dict, context: "ProviderContext" = None):
        super().__init__(config, context)
        self.logger = get_logger("MockOutputProvider")
        self.received_intents = []

        self.logger.info("MockOutputProvider初始化完成")

    async def execute(self, intent: "Intent") -> None:
        """记录收到的 Intent"""
        self.received_intents.append(intent)
        response_text = intent.response_text[:50] if intent.response_text else ""
        self.logger.debug(f"收到 Intent: response_text={response_text}...")

    async def init(self) -> None:
        """初始化 Provider"""
        self.logger.info("MockOutputProvider启动完成")

    async def cleanup(self) -> None:
        """清理 Provider"""
        self.logger.info(f"MockOutputProvider清理完成，共收到 {len(self.received_intents)} 条 Intent")

    def get_received_intents(self):
        """获取收到的所有 Intent（用于测试断言）"""
        return self.received_intents

    def clear_received_intents(self):
        """清空记录（用于测试）"""
        self.received_intents.clear()


# 注册到 ProviderRegistry
ProviderRegistry.register_output("mock", MockOutputProvider, source="builtin:mock")

__all__ = ["MockOutputProvider"]
