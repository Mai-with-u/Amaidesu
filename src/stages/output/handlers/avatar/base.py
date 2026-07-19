"""
AvatarHandlerBase - 虚拟形象 Handler 抽象基类

定义所有 Avatar Handler 的通用处理流程:
1. 适配 Intent 为平台参数 (_adapt_intent,返回 None = skip)
2. 渲染到平台 (_render_to_platform)
3. 连接/断开管理
4. **per-handler 完成事件自动 emit**(OUTPUT_HANDLER_COMPLETED),在 handle() 的 finally 中,
   保证异常路径也发出,让 OutputHandlerManager 可以聚合各 handler 的完成事件。

LLM 翻译层已删除:Intent 已经是结构化 emotion(action) 形式,不再需要 LLM 翻译
自然语言到平台键。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, Optional

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.decision import IntentPayload
from src.modules.events.payloads.output import OutputHandlerCompletedPayload
from src.modules.logging import get_logger
from src.modules.streaming.audio_stream_channel import AudioStreamChannel

if TYPE_CHECKING:
    from src.modules.types import Intent


class AvatarHandlerBase(ABC):
    """虚拟形象 Handler 抽象基类(LLM 翻译已删除)。

    使用构造器注入获取依赖,子类只需实现平台特定的适配和渲染逻辑。

    `_adapt_intent` 返回 `None` 表示跳过(参数校验失败 / 静默 skip),返回 dict
    表示继续渲染。
    """

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: EventBus,
        audio_stream_channel: Optional[AudioStreamChannel] = None,
    ):
        self.config = config
        self.event_bus = event_bus
        self.audio_stream_channel = audio_stream_channel
        self.logger = get_logger(self.__class__.__name__)
        self._is_connected = False
        self._has_started = False
        self._dispatch_subscribed = False

    async def handle(self, intent: "Intent"):
        if not self._is_connected:
            self.logger.warning("未连接,跳过渲染")
            await self._emit_completed(intent, success=True)
            return

        success = True
        try:
            params = await self._adapt_intent(intent)
            if params is None:
                self.logger.debug("adapter 返回 None,跳过本次渲染")
                return
            await self._render_to_platform(params)
        except Exception as e:
            success = False
            self.logger.error(f"渲染失败: {e}", exc_info=True)
        finally:
            await self._emit_completed(intent, success=success)

    async def _emit_completed(self, intent: "Intent", success: bool = True) -> None:
        """emit 一个 OUTPUT_HANDLER_COMPLETED 事件给聚合者。

        用 `self.__class__.__name__` 作为 handler_name(与 manager 的 active_handler_names
        命名一致,即 `type(h).__name__`),从 Intent.metadata.intent_id 拿 intent_id。
        无 event_bus 或 intent 缺 metadata 时降级到 unknown 让 watchdog 兜底。
        """
        if self.event_bus is None:
            return
        try:
            intent_id = intent.metadata.intent_id
        except AttributeError:
            intent_id = "unknown"
        await self.event_bus.emit(
            CoreEvents.OUTPUT_HANDLER_COMPLETED,
            OutputHandlerCompletedPayload(
                handler_name=self.__class__.__name__,
                intent_id=intent_id,
                success=success,
            ),
            source=self.__class__.__name__,
        )

    @abstractmethod
    async def _adapt_intent(self, intent: "Intent") -> Optional[Dict[str, Any]]:
        """适配 Intent 为平台特定参数。

        返回:
        - `None` → 跳过本次渲染(参数校验失败等静默情况)
        - `Dict[str, Any]` → 继续渲染
        """
        ...

    @abstractmethod
    async def _render_to_platform(self, params: Dict[str, Any]) -> None: ...

    async def init(self):
        if self.event_bus and not getattr(self, "_dispatch_subscribed", False):
            self.event_bus.on(
                CoreEvents.OUTPUT_INTENT_DISPATCHED,
                self._handle_intent_dispatched,
                IntentPayload,
            )
            self._dispatch_subscribed = True
            self.logger.debug(f"{self.__class__.__name__} 已订阅 {CoreEvents.OUTPUT_INTENT_DISPATCHED}")

        await self._connect()
        self._has_started = True
        self.logger.info(f"{self.__class__.__name__} 已启动")

    async def _handle_intent_dispatched(self, event_name: str, payload: IntentPayload, source: str) -> None:
        try:
            intent = payload.to_intent()
            await self.handle(intent)
        except Exception as e:
            self.logger.error(f"处理 Intent 派发事件失败: {e}", exc_info=True)

    async def cleanup(self):
        if not self._has_started:
            return
        if self.event_bus and getattr(self, "_dispatch_subscribed", False):
            try:
                self.event_bus.off(
                    CoreEvents.OUTPUT_INTENT_DISPATCHED,
                    self._handle_intent_dispatched,
                )
            except Exception as e:
                self.logger.warning(f"取消订阅 {CoreEvents.OUTPUT_INTENT_DISPATCHED} 失败: {e}")
            finally:
                self._dispatch_subscribed = False

        sticker_handler = getattr(self, "_on_sticker_command", None)
        if self.event_bus and getattr(self, "_sticker_subscribed", False) and sticker_handler is not None:
            try:
                self.event_bus.off(
                    CoreEvents.OUTPUT_STICKER_COMMAND,
                    sticker_handler,
                )
            except Exception as e:
                self.logger.warning(f"取消订阅 {CoreEvents.OUTPUT_STICKER_COMMAND} 失败: {e}")
            finally:
                self._sticker_subscribed = False

        await self._disconnect()
        self.logger.info(f"{self.__class__.__name__} 已停止")

    @abstractmethod
    async def _connect(self) -> None: ...

    @abstractmethod
    async def _disconnect(self) -> None: ...
