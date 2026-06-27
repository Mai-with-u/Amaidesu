"""
Router 适配器

职责:
- 封装 maim_message Router
- 提供简化的发送/接收接口
"""

from typing import Any, Callable, Dict, Optional

from maim_message import MessageBase, Router

from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger


class RouterAdapter:
    """封装 maim_message Router，提供简化的发送和消息处理回调注册接口。"""

    def __init__(self, router: Router, event_bus: Optional[EventBus] = None):
        self._router = router
        self._event_bus = event_bus
        self.logger = get_logger("RouterAdapter")

    async def send(self, message: MessageBase) -> None:
        if not self._router:
            self.logger.error("Router 未初始化，无法发送消息")
            raise RuntimeError("Router 未初始化")

        try:
            self.logger.debug(f"准备发送消息: {message.message_info.message_id}")
            await self._router.send_message(message)
            self.logger.debug(f"消息 {message.message_info.message_id} 已发送")
        except Exception as e:
            self.logger.error(f"发送消息时发生错误: {e}", exc_info=True)
            raise ConnectionError(f"发送消息失败: {e}") from e

    def register_handler(self, handler: Callable[[Dict[str, Any]], Any]):
        if not self._router:
            self.logger.error("Router 未初始化，无法注册处理器")
            return

        self._router.register_class_handler(handler)
        self.logger.info("消息处理器已注册")

    @property
    def router(self) -> Optional[Router]:
        return self._router
