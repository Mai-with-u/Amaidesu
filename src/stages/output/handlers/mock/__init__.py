"""Mock Output Handler - 用于测试"""

from typing import TYPE_CHECKING, Any, Dict, Literal

from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.stages.output.registry import handler

if TYPE_CHECKING:
    from src.modules.types import Intent


@handler("mock")
class MockOutputHandler:
    """
    模拟输出 Handler（用于测试）

    记录收到的所有 Intent，不进行实际渲染。
    """

    class ConfigSchema(BaseConfig):
        """模拟输出 Handler 配置（用于测试）"""

        type: Literal["mock"] = "mock"

    def __init__(self, config: Dict[str, Any], event_bus: EventBus):
        self.config = config
        self.event_bus = event_bus
        self.logger = get_logger("MockOutputHandler")
        self.received_intents: list = []

        self.logger.info("MockOutputHandler 初始化完成")

    async def handle(self, intent: "Intent") -> None:
        """记录收到的 Intent"""
        self.received_intents.append(intent)
        speech = intent.speech[:50] if intent.speech else ""
        self.logger.debug(f"收到 Intent: speech={speech}...")

    async def setup(self) -> None:
        """初始化 Handler"""
        self.logger.info("MockOutputHandler 启动完成")

    async def cleanup(self) -> None:
        """清理 Handler"""
        self.logger.info(f"MockOutputHandler 清理完成，共收到 {len(self.received_intents)} 条 Intent")

    def get_received_intents(self):
        """获取收到的所有 Intent（用于测试断言）"""
        return self.received_intents

    def clear_received_intents(self):
        """清空记录（用于测试）"""
        self.received_intents.clear()


__all__ = ["MockOutputHandler"]
