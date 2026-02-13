"""
输出Provider接口

定义了输出域（Output Domain）的Provider接口。
OutputProvider负责将Intent渲染到目标设备。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from src.modules.streaming.audio_stream_channel import AudioStreamChannel
    from src.modules.types import Intent
    from src.modules.events.payloads.decision import IntentPayload


class OutputProvider(ABC):
    """
    输出Provider抽象基类

    职责: 将Intent渲染到目标设备

    生命周期:
    1. 实例化(__init__)
    2. 启动(start()) - 订阅 OUTPUT_INTENT 事件
    3. 渲染(_render_internal()) - 处理 Intent
    4. 停止(stop()) - 释放资源

    Attributes:
        config: Provider配置
        event_bus: EventBus实例
        is_started: 是否已启动
        priority: 事件处理优先级
        audio_stream_channel: AudioStreamChannel实例
    """

    priority: int = 50  # 事件处理优先级

    def __init__(self, config: dict):
        self.config = config
        self.event_bus = None
        self.is_started = False
        self._audio_stream_channel: Optional["AudioStreamChannel"] = None

    @property
    def audio_stream_channel(self) -> Optional["AudioStreamChannel"]:
        return self._audio_stream_channel

    async def start(self, event_bus, audio_stream_channel: Optional["AudioStreamChannel"] = None):
        """
        启动Provider，订阅 OUTPUT_INTENT 事件

        Args:
            event_bus: EventBus实例
            audio_stream_channel: 可选的AudioStreamChannel实例
        """
        from src.modules.events.names import CoreEvents
        from src.modules.events.payloads.decision import IntentPayload

        self.event_bus = event_bus
        self._audio_stream_channel = audio_stream_channel
        event_bus.on(CoreEvents.OUTPUT_INTENT, self._on_intent, model_class=IntentPayload, priority=self.priority)

        try:
            await self._start_internal()
            self.is_started = True
        except Exception:
            raise

    async def _on_intent(self, event_name: str, payload: "IntentPayload", source: str):
        """
        接收过滤后的Intent

        Args:
            event_name: 事件名称
            payload: IntentPayload 对象
            source: 事件源
        """
        intent = payload.to_intent()
        await self._render_internal(intent)

    @abstractmethod
    async def _render_internal(self, intent: "Intent"):
        """
        渲染Intent（子类必须实现）

        Args:
            intent: 意图对象
        """
        pass

    async def _start_internal(self):  # noqa: B027
        """内部启动逻辑(子类可选重写)"""
        ...

    async def stop(self):
        """停止Provider并清理资源"""
        from src.modules.events.names import CoreEvents

        if not self.is_started:
            return

        try:
            if self.event_bus:
                self.event_bus.off(CoreEvents.OUTPUT_INTENT, self._on_intent)
            await self._stop_internal()
            self.is_started = False
        except Exception:
            pass

    async def _stop_internal(self):  # noqa: B027
        """内部停止逻辑(子类可选重写)"""
        ...

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "is_started": self.is_started,
            "type": "output_provider",
            "priority": self.priority,
        }
