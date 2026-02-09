"""
Router 适配器

职责:
- 封装 maim_message Router
- 提供简化的发送/接收接口
"""

from typing import Callable, Dict, Any, Optional, TYPE_CHECKING

from maim_message import MessageBase

from src.core.utils.logger import get_logger

if TYPE_CHECKING:
    from maim_message import Router
    from src.core.event_bus import EventBus
    from src.domains.decision.intent import Intent


class RouterAdapter:
    """
    Router 适配器

    封装 maim_message Router，提供简化的发送/接收接口。
    """

    def __init__(self, router: "Router", event_bus: Optional["EventBus"] = None):
        """
        初始化 Router 适配器

        Args:
            router: maim_message Router 实例
            event_bus: EventBus 实例（用于发布接收到的消息）
        """
        self._router = router
        self._event_bus = event_bus
        self.logger = get_logger("RouterAdapter")

    async def send(self, message: MessageBase) -> None:
        """
        发送消息

        Args:
            message: 要发送的消息

        Raises:
            RuntimeError: 如果 Router 未初始化
            ConnectionError: 如果发送失败
        """
        if not self._router:
            self.logger.error("Router 未初始化，无法发送消息")
            raise RuntimeError("Router 未初始化")

        try:
            self.logger.debug(f"准备发送消息: {message.message_info.message_id}")
            await self._router.send_message(message)
            self.logger.info(f"消息 {message.message_info.message_id} 已发送")
        except Exception as e:
            self.logger.error(f"发送消息时发生错误: {e}", exc_info=True)
            raise ConnectionError(f"发送消息失败: {e}") from e

    async def receive(self) -> Optional[MessageBase]:
        """
        接收消息

        注意：这个方法实际上不会阻塞等待，因为 Router 接收到的消息
        会通过回调函数处理。这个方法主要是为了保持接口一致性。

        Returns:
            None（实际消息通过回调处理）
        """
        self.logger.debug("receive() 方法被调用（实际消息通过回调处理）")
        return None

    def register_message_handler(self, handler: Callable[[Dict[str, Any]], Any]):
        """
        注册消息处理回调

        Args:
            handler: 消息处理函数，接收消息字典作为参数（可以是异步函数）
        """
        if not self._router:
            self.logger.error("Router 未初始化，无法注册处理器")
            return

        self._router.register_class_handler(handler)
        self.logger.info("消息处理器已注册")

    async def send_action_suggestion(self, intent: "Intent") -> None:
        """
        发送 Action 建议到 MaiBot（fire-and-forget）

        Args:
            intent: Intent 对象，包含建议的动作列表
        """
        from .message_schema import ActionSuggestionMessage

        if not intent.suggested_actions:
            self.logger.debug("Intent 无建议动作，跳过发送")
            return

        message = ActionSuggestionMessage(
            intent_id=intent.id,
            original_text=intent.original_text,
            response_text=intent.response_text,
            emotion=intent.emotion.value,
            suggested_actions=[sa.model_dump() for sa in intent.suggested_actions],
        )

        await self.send(message.to_message_base())
        self.logger.info(f"已发送 Action 建议: {len(intent.suggested_actions)} 个动作")

    @property
    def router(self) -> Optional["Router"]:
        """获取 Router 实例"""
        return self._router
