"""
AvatarHandlerBase - 虚拟形象 Handler 抽象基类

定义所有 Avatar Handler 的通用处理流程:
1. 适配 Intent 为平台参数 (_adapt_intent,返回 None = skip)
2. 渲染到平台 (_render_to_platform)
3. 连接/断开管理

注:Manager 现在直接调用 `handler.handle(intent)`,Handler 不再订阅
Intent 派发事件,也不再 emit 完成事件给 Manager 聚合。
Sticker 指令事件订阅为 handler 间通信,保留在子类中处理。

LLM 翻译层已删除:Intent 已经是结构化 emotion(action) 形式,不再需要 LLM 翻译
自然语言到平台键。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, Optional

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
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

    async def handle(self, intent: "Intent"):
        if not self._is_connected:
            self.logger.warning("未连接,跳过渲染")
            return

        try:
            params = await self._adapt_intent(intent)
            if params is None:
                self.logger.debug("adapter 返回 None,跳过本次渲染")
                return
            await self._render_to_platform(params)
        except Exception as e:
            self.logger.error(f"渲染失败: {e}", exc_info=True)

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
        await self._connect()
        self._has_started = True
        self.logger.info(f"{self.__class__.__name__} 已启动")

    async def cleanup(self):
        if not self._has_started:
            return

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
