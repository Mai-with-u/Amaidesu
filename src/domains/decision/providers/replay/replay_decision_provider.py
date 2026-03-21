"""
ReplayDecisionProvider - 输入重放决策提供者

职责:
- 将输入消息直接重放到输出域（不经过任何决策处理）
- 用于调试和测试，验证输入域到输出域的完整数据流

使用场景:
- 调试时需要验证数据流是否正常工作
- 不需要 AI 决策，只需要将用户输入直接传递到输出
"""

import time
from typing import TYPE_CHECKING, Any, Dict, Literal

from pydantic import Field

from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.di.context import ProviderContext
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import IntentPayload
from src.modules.logging import get_logger
from src.modules.types import Intent, IntentMetadata
from src.modules.types.base.decision_provider import DecisionProvider

if TYPE_CHECKING:
    from src.modules.types.base.normalized_message import NormalizedMessage


class ReplayDecisionProvider(DecisionProvider):
    """
    输入重放决策提供者

    直接将 NormalizedMessage 重放到输出域，不做任何处理。
    用于调试和测试数据流。

    配置示例:
        ```toml
        [providers.decision]
        active_provider = "replay"

        [replay]
        add_default_action = true
        ```
    """

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        return {"layer": "decision", "name": "replay", "class": cls, "source": "builtin:replay"}

    class ConfigSchema(BaseProviderConfig):
        type: Literal["replay"] = "replay"
        add_default_action: bool = Field(default=True, description="是否添加默认动作")

    def __init__(self, config: Dict[str, Any], context: "ProviderContext"):
        super().__init__(config, context)
        self.typed_config = self.ConfigSchema(**config)
        self.logger = get_logger("ReplayDecisionProvider")
        self.add_default_action = self.typed_config.add_default_action
        self._total_messages = 0

    async def init(self) -> None:
        self.logger.info("初始化 ReplayDecisionProvider...")

    async def decide(self, normalized_message: "NormalizedMessage") -> None:
        self._total_messages += 1
        self.logger.debug(f"重放消息: {normalized_message.text[:50]}...")

        action = "眨眼" if self.add_default_action else None

        intent = Intent(
            emotion="平静",
            action=action,
            speech=normalized_message.text,
            context=f"来源: {normalized_message.source}",
            metadata=IntentMetadata(
                source_id=normalized_message.source,
                decision_time=int(time.time() * 1000),
                parser_type="replay",
                replay_count=self._total_messages,
            ),
        )

        await self._publish_intent(intent)
        self.logger.debug(f"已重放消息 (总计: {self._total_messages})")

    async def _publish_intent(self, intent: Intent) -> None:
        if not self.event_bus:
            self.logger.error("EventBus 未初始化，无法发布事件")
            return

        await self.event_bus.emit(
            CoreEvents.DECISION_INTENT_GENERATED,
            IntentPayload.from_intent(intent, "replay"),
            source="ReplayDecisionProvider",
        )

    async def cleanup(self) -> None:
        self.logger.info(f"ReplayDecisionProvider 已清理，共重放 {self._total_messages} 条消息")
