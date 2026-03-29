"""
Mock 决策 Provider（用于测试）
"""

from typing import Any, Dict, List, Optional

from src.modules.types import Intent, IntentMetadata
from src.modules.types.base.decision_provider import DecisionProvider
from src.modules.types.base.normalized_message import NormalizedMessage


class MockDecisionProvider(DecisionProvider):
    """Mock 决策 Provider（用于测试）"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config or {})
        self.responses: List[Dict[str, Any]] = []  # 预设的响应列表
        self.call_count = 0  # 调用计数
        self.last_message: Optional[NormalizedMessage] = None

    def add_response(self, text: str, emotion: str = "neutral"):
        """添加预设响应"""
        self.responses.append(
            {
                "text": text,
                "emotion": emotion,
            }
        )

    async def decide(self, message: NormalizedMessage) -> Optional[Intent]:
        """决策（返回预设响应或默认响应）"""
        self.call_count += 1
        self.last_message = message

        if not self.responses:
            # 默认响应
            return Intent(
                speech="这是一个模拟回复",
                emotion="neutral",
                action=None,
                context=None,
                metadata=IntentMetadata(
                    source_id="mock",
                    decision_time=0,
                ),
            )

        response = self.responses.pop(0)
        return Intent(
            speech=response["text"],
            emotion=response["emotion"],
            action=None,
            context=None,
            metadata=IntentMetadata(
                source_id="mock",
                decision_time=0,
            ),
        )

    def reset(self):
        """重置状态"""
        self.responses.clear()
        self.call_count = 0
        self.last_message = None
