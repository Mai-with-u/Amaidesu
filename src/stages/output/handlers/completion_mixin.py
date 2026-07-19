"""
CompletionEmitterMixin - 为独立 OutputHandler 提供 _emit_completed 方法。

AudioHandlerBase 和 AvatarHandlerBase 已内置该方法，子类无需重复。
独立 Handler（StickerHandler / SubtitleHandler / DebugConsoleHandler /
ObsControlHandler）通过继承此 Mixin 获得相同实现，消除重复代码。
"""

from typing import TYPE_CHECKING

from src.modules.events.names import CoreEvents
from src.modules.events.payloads import OutputHandlerCompletedPayload

if TYPE_CHECKING:
    from src.modules.types import Intent


class CompletionEmitterMixin:
    """为独立 OutputHandler 提供 _emit_completed 方法。

    要求宿主类有 self.event_bus 属性（EventBus 实例或 None）。
    """

    async def _emit_completed(self, intent: "Intent", success: bool = True) -> None:
        """emit 一个 OUTPUT_HANDLER_COMPLETED 事件给聚合者(OutputHandlerManager)。

        handler_name 用 `self.__class__.__name__`（与 Manager 端的 `type(h).__name__`
        一致），intent_id 从 `intent.metadata.intent_id` 取。无 event_bus 时静默跳过。
        老 Intent 结构缺 metadata 时降级到 "unknown" 让 watchdog 兜底。
        """
        if self.event_bus is None:  # type: ignore[attr-defined]
            return
        try:
            intent_id = intent.metadata.intent_id
        except AttributeError:
            intent_id = "unknown"
        await self.event_bus.emit(  # type: ignore[attr-defined]
            CoreEvents.OUTPUT_HANDLER_COMPLETED,
            OutputHandlerCompletedPayload(
                handler_name=self.__class__.__name__,
                intent_id=intent_id,
                success=success,
            ),
            source=self.__class__.__name__,
        )
