"""Mock Decision Provider - 用于测试"""
from typing import Optional, Dict, Any
from src.core.base.decision_provider import DecisionProvider
from src.core.base.normalized_message import NormalizedMessage
from src.layers.decision.intent import Intent
from src.utils.logger import get_logger


class MockDecisionProvider(DecisionProvider):
    """
    模拟决策Provider（用于测试）

    简单返回预设的响应，不进行实际决策逻辑。
    """

    def __init__(self, config: dict):
        self.config = config
        self.logger = get_logger("MockDecisionProvider")
        self.default_response = config.get("default_response", "这是模拟的回复")
        self.call_count = 0

        self.logger.info("MockDecisionProvider初始化完成")

    async def decide(self, message: NormalizedMessage) -> Optional[Intent]:
        """返回预设的响应"""
        self.call_count += 1
        
        # 简单的模拟逻辑
        response_text = self.default_response
        if message.text:
            response_text = f"[模拟回复] {message.text}"
        
        return Intent(
            text=response_text,
            expression="neutral",
            confidence=1.0,
            metadata={"mock": True, "call_count": self.call_count}
        )

    async def setup(self) -> None:
        """设置Provider"""
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


from src.layers.rendering.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_decision("mock", MockDecisionProvider, source="builtin:mock")

__all__ = ["MockDecisionProvider"]
