"""
ReplayDecisionProvider - 输入重放决策提供者

职责:
- 将输入消息直接重放到输出域（不经过任何决策处理）
- 用于调试和测试，验证输入域到输出域的完整数据流

使用场景:
- 调试时需要验证数据流是否正常工作
- 不需要 AI 决策，只需要将用户输入直接传递到输出
"""

from typing import TYPE_CHECKING, Any, Dict, Literal

from pydantic import Field

from src.modules.di.context import ProviderContext
from src.modules.types import ActionType, EmotionType, Intent, IntentAction, SourceContext
from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
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
        # 是否添加默认动作（眨眼），默认 true
        add_default_action = true
        ```
    """

    class ConfigSchema(BaseProviderConfig):
        """Replay决策Provider配置Schema

        将输入消息直接重放到输出域。
        """

        type: Literal["replay"] = "replay"
        add_default_action: bool = Field(default=True, description="是否添加默认动作（眨眼）")

    def __init__(self, config: Dict[str, Any], context: "ProviderContext"):
        """
        初始化 ReplayDecisionProvider

        Args:
            config: 配置字典
            context: 依赖注入上下文
        """
        super().__init__(config, context)

        # 使用 Pydantic Schema 验证配置
        self.typed_config = self.ConfigSchema(**config)
        self.logger = get_logger("ReplayDecisionProvider")

        # 配置
        self.add_default_action = self.typed_config.add_default_action

        # 统计信息
        self._total_messages = 0

    async def init(self) -> None:
        """初始化 ReplayDecisionProvider"""
        self.logger.info("初始化 ReplayDecisionProvider...")

    async def decide(self, normalized_message: "NormalizedMessage") -> None:
        """
        重放输入消息

        将 NormalizedMessage 的内容直接转换为 Intent 并发布到输出域。

        Args:
            normalized_message: 标准化消息
        """
        self._total_messages += 1
        self.logger.debug(f"重放消息: {normalized_message.text[:50]}...")

        # 构建动作列表
        actions: list[IntentAction] = []
        if self.add_default_action:
            actions.append(
                IntentAction(
                    type=ActionType.BLINK,
                    params={},
                    priority=30,
                )
            )

        # 创建 Intent，将原始文本作为响应文本
        intent = Intent(
            original_text=normalized_message.text,
            response_text=normalized_message.text,  # 重放：响应文本 = 原始文本
            emotion=EmotionType.NEUTRAL,
            actions=actions,
            metadata={"parser": "replay", "replay_count": self._total_messages},
        )

        # 构建 SourceContext
        source_context = SourceContext(
            source=normalized_message.source,
            data_type=normalized_message.data_type,
            user_id=normalized_message.user_id,
            user_nickname=normalized_message.metadata.get("user_nickname"),
            importance=normalized_message.importance,
        )
        intent.source_context = source_context

        # 发布 decision.intent 事件
        await self._publish_intent(intent)

        self.logger.debug(f"已重放消息 (总计: {self._total_messages})")

    async def _publish_intent(self, intent: Intent) -> None:
        """
        通过 event_bus 发布 decision.intent 事件

        Args:
            intent: 要发布的 Intent
        """
        from src.modules.events.names import CoreEvents
        from src.modules.events.payloads import IntentPayload

        if not self.event_bus:
            self.logger.error("EventBus 未初始化，无法发布事件")
            return

        await self.event_bus.emit(
            CoreEvents.DECISION_INTENT,
            IntentPayload.from_intent(intent, "replay"),
            source="ReplayDecisionProvider",
        )

    async def cleanup(self) -> None:
        """清理资源"""
        self.logger.info(f"ReplayDecisionProvider 已清理，共重放 {self._total_messages} 条消息")
