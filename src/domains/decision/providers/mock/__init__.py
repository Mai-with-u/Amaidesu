"""Mock Decision Provider - 用于测试"""

from typing import Optional, Literal

from pydantic import Field

# 必须在类型导入前导入这些
from src.core.base.decision_provider import DecisionProvider
from src.core.base.normalized_message import NormalizedMessage
from src.domains.decision.intent import Intent
from src.core.utils.logger import get_logger
from src.core.provider_registry import ProviderRegistry
from src.services.config.schemas.schemas.base import BaseProviderConfig


class MockDecisionProvider(DecisionProvider):
    """
    模拟决策Provider（用于测试）

    简单返回预设的响应，不进行实际决策逻辑。

    配置示例:
        ```toml
        [decision.mock]
        default_response = "这是模拟的回复"
        ```
    """

    class ConfigSchema(BaseProviderConfig):
        """模拟决策Provider配置Schema

        用于测试的模拟Provider，返回预设响应。
        """

        type: Literal["mock"] = "mock"
        default_response: str = Field(default="这是模拟的回复", description="默认响应文本")

    def __init__(self, config: dict):
        # 使用 Pydantic Schema 验证配置
        self.typed_config = self.ConfigSchema(**config)
        self.logger = get_logger("MockDecisionProvider")
        self.default_response = self.typed_config.default_response
        self.call_count = 0

        self.logger.info("MockDecisionProvider初始化完成")

    async def decide(self, message: NormalizedMessage) -> Optional[Intent]:
        """返回预设的响应"""
        self.call_count += 1

        # 简单的模拟逻辑
        if message.text:
            # 如果有文本，总是使用 "[模拟回复] {message.text}" 格式
            response_text = f"[模拟回复] {message.text}"
        else:
            # 如果没有文本，使用 default_response
            response_text = self.default_response

        from src.domains.decision.intent import Intent, EmotionType

        return Intent(
            original_text=message.text,
            response_text=response_text,
            emotion=EmotionType.NEUTRAL,
            actions=[],
            metadata={"mock": True, "call_count": self.call_count},
        )

    async def setup(self, event_bus, config: Optional[dict] = None, dependencies: Optional[dict] = None) -> None:
        """设置Provider"""
        self.event_bus = event_bus
        if config:
            # 使用 Pydantic Schema 验证配置
            self.typed_config = self.ConfigSchema(**config)
            # 更新 default_response
            self.default_response = self.typed_config.default_response
        self._dependencies = dependencies or {}
        self.logger.info("MockDecisionProvider设置完成")

    async def cleanup(self) -> None:
        """清理Provider"""
        self.logger.info(f"MockDecisionProvider清理完成，共调用 {self.call_count} 次")

    def get_call_count(self):
        """获取调用次数（用于测试断言）"""
        return self.call_count

    def reset_call_count(self):
        """重置调用次数（用于测试）"""
        self.call_count = 0


# 注册到 ProviderRegistry
ProviderRegistry.register_decision("mock", MockDecisionProvider, source="builtin:mock")

__all__ = ["MockDecisionProvider"]
