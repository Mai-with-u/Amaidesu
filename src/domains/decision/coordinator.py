"""
DecisionCoordinator - 决策域协调器

职责:
- 订阅 Input Domain 的 NORMALIZATION_MESSAGE_READY 事件
- 构建 SourceContext
- 调用 DecisionProviderManager 进行决策
- 发布 DECISION_INTENT_GENERATED 事件到 Output Domain
- 确保 3 域架构的数据流正确性
"""

from typing import TYPE_CHECKING

from src.core.utils.logger import get_logger
from src.core.events.names import CoreEvents

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from src.core.events.payloads.input import MessageReadyPayload
    from src.domains.decision.provider_manager import DecisionProviderManager


class DecisionCoordinator:
    """
    决策协调器 (Decision Domain: 协调器)

    职责:
    - 订阅 normalization.message_ready 事件（来自 Input Domain）
    - 构建 SourceContext 传递消息来源信息
    - 调用 DecisionProviderManager.decide() 进行决策
    - 发布 decision.intent_generated 事件（到 Output Domain）

    架构约束（3域架构）:
    - 只订阅 Input Domain 的事件
    - 只发布到 Output Domain
    - 不订阅 Output Domain 的事件（避免循环依赖）
    - 不订阅其他 Decision Domain 事件

    使用示例:
        ```python
        # 初始化
        coordinator = DecisionCoordinator(event_bus, provider_manager)

        # 启动（订阅事件）
        await coordinator.setup()

        # 自动处理 NormalizedMessage
        # EventBus.emit(NORMALIZATION_MESSAGE_READY) -> coordinator._on_normalized_message_ready()

        # 清理
        await coordinator.cleanup()
        ```
    """

    def __init__(
        self,
        event_bus: "EventBus",
        provider_manager: "DecisionProviderManager",
    ):
        """
        初始化 DecisionCoordinator

        Args:
            event_bus: EventBus 实例
            provider_manager: DecisionProviderManager 实例
        """
        self.event_bus = event_bus
        self._provider_manager = provider_manager
        self.logger = get_logger("DecisionCoordinator")
        self._event_subscribed = False

    async def setup(self) -> None:
        """
        启动协调器

        订阅 normalization.message_ready 事件（防止重复订阅）。
        """
        if not self._event_subscribed:
            from src.core.events.payloads.input import MessageReadyPayload

            self.event_bus.on(
                CoreEvents.NORMALIZATION_MESSAGE_READY,
                self._on_normalized_message_ready,
                model_class=MessageReadyPayload,
            )
            self._event_subscribed = True
            self.logger.info(f"DecisionCoordinator 已订阅 '{CoreEvents.NORMALIZATION_MESSAGE_READY}' 事件（类型化）")
        else:
            self.logger.debug(
                f"DecisionCoordinator 已订阅过 '{CoreEvents.NORMALIZATION_MESSAGE_READY}' 事件，跳过重复订阅"
            )

    async def cleanup(self) -> None:
        """
        清理资源

        取消事件订阅。
        """
        if self._event_subscribed:
            self.event_bus.off(CoreEvents.NORMALIZATION_MESSAGE_READY, self._on_normalized_message_ready)
            self._event_subscribed = False
            self.logger.debug("DecisionCoordinator 已取消事件订阅")

    async def _on_normalized_message_ready(self, event_name: str, payload: "MessageReadyPayload", source: str):
        """
        处理 normalization.message_ready 事件（类型化）

        当 InputDomain 生成 NormalizedMessage 时：
        1. 构建 SourceContext
        2. 调用 DecisionProviderManager.decide()
        3. 将 SourceContext 注入 Intent
        4. 发布 decision.intent_generated 事件（3域架构）

        Args:
            event_name: 事件名称 (CoreEvents.NORMALIZATION_MESSAGE_READY)
            payload: 类型化的事件数据（MessageReadyPayload 对象）
            source: 事件源
        """
        # 直接从 payload.message 获取 NormalizedMessage 对象
        message_data = payload.message
        if not message_data:
            self.logger.warning("收到空的 NormalizedMessage 事件")
            return

        # 兼容处理：message 字段可以是 NormalizedMessage 对象或字典
        from src.core.base.normalized_message import NormalizedMessage
        from src.domains.decision.intent import SourceContext

        if isinstance(message_data, dict):
            # 字典格式：使用 from_dict 方法重建或直接使用字典
            normalized_dict = message_data
            text = normalized_dict.get("text", "")
            source = normalized_dict.get("source", "")
            data_type = normalized_dict.get("data_type", "")
            importance = normalized_dict.get("importance", 0.5)
            metadata = normalized_dict.get("metadata", {})
            user_id = normalized_dict.get("user_id")
            user_nickname = metadata.get("user_nickname")

            # 构建 SourceContext
            source_context = SourceContext(
                source=source,
                data_type=data_type,
                user_id=user_id,
                user_nickname=user_nickname,
                importance=importance,
                extra={k: v for k, v in metadata.items() if k not in ("type", "timestamp", "source")},
            )

            # 尝试重建 NormalizedMessage 对象用于决策
            try:
                normalized = NormalizedMessage.model_validate(normalized_dict)
            except Exception:
                # 如果重建失败，使用字典数据创建一个临时对象
                normalized = NormalizedMessage(
                    text=text,
                    content=None,
                    source=source,
                    data_type=data_type,
                    importance=importance,
                    metadata=metadata,
                    timestamp=normalized_dict.get("timestamp", 0.0),
                )
        else:
            # 对象格式：直接使用
            normalized = message_data

            # 构建 SourceContext
            source_context = SourceContext(
                source=normalized.source,
                data_type=normalized.data_type,
                user_id=normalized.user_id if hasattr(normalized, "user_id") else None,
                user_nickname=normalized.metadata.get("user_nickname"),
                importance=normalized.importance,
                extra={k: v for k, v in normalized.metadata.items() if k not in ("type", "timestamp", "source")},
            )

        try:
            self.logger.debug(f"收到 NormalizedMessage: {normalized.text[:50]}...")

            # 调用 DecisionProviderManager 进行决策
            intent = await self._provider_manager.decide(normalized)

            # 注入 source_context（如果 Provider 未设置）
            if intent.source_context is None:
                intent.source_context = source_context

            # 获取当前 Provider 名称
            provider_name = self._provider_manager.get_current_provider_name() or "unknown"

            # 发布 decision.intent_generated 事件（3域架构）
            from src.core.events.payloads import IntentPayload

            await self.event_bus.emit(
                CoreEvents.DECISION_INTENT_GENERATED,
                IntentPayload.from_intent(intent, provider_name),
                source="DecisionCoordinator",
            )
            self.logger.debug(f"已发布 decision.intent_generated 事件: {intent.response_text[:50]}...")
        except Exception as e:
            self.logger.error(f"处理 NormalizedMessage 时出错: {e}", exc_info=True)
