"""
输出Provider接口

定义了输出域（Output Domain）的Provider接口。
OutputProvider负责将Intent渲染到目标设备。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, Optional

from src.modules.di.context import ProviderContext
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.decision import IntentPayload

if TYPE_CHECKING:
    from src.modules.streaming.audio_stream_channel import AudioStreamChannel
    from src.modules.types import Intent


class OutputProvider(ABC):
    """
    输出Provider抽象基类 - 依赖注入版本

    职责: 将Intent渲染到目标设备

    生命周期:
    1. 实例化(__init__)
    2. 启动(start()) - 订阅 OUTPUT_INTENT 事件，内部调用 init()
    3. 执行(execute()) - 处理 Intent
    4. 停止(stop()) - 取消订阅，内部调用 cleanup()

    Attributes:
        config: Provider配置
        context: ProviderContext实例（依赖注入）
        event_bus: EventBus实例（从context获取）
        is_started: 是否已启动
        priority: 事件处理优先级
        audio_stream_channel: AudioStreamChannel实例（从context获取）
    """

    priority: int = 50  # 事件处理优先级

    def __init__(
        self,
        config: dict,
        context: ProviderContext = None,
    ):
        if context is None:
            raise ValueError("OutputProvider 必须接收 context 参数")

        self.config = config
        self.context = context
        self.is_started = False

    @property
    def event_bus(self):
        return self.context.event_bus

    @property
    def audio_stream_channel(self) -> Optional["AudioStreamChannel"]:
        return self.context.audio_stream_channel

    async def init(self):  # noqa: B027
        """
        初始化 Provider（子类可重写）

        执行初始化逻辑，如加载资源、建立连接等。
        在 start() 方法中调用。
        """
        pass

    async def start(self):
        """
        启动 Provider，订阅 OUTPUT_INTENT_READY 事件

        依赖已在构造时通过 context 注入。
        """
        if self.event_bus:
            self.event_bus.on(
                CoreEvents.OUTPUT_INTENT_READY, self._on_intent, model_class=IntentPayload, priority=self.priority
            )

        await self.init()
        self.is_started = True

    async def _on_intent(self, event_name: str, payload: "IntentPayload", source: str):
        """
        接收过滤后的 Intent 事件

        Args:
            event_name: 事件名称
            payload: IntentPayload 对象
            source: 事件源
        """
        intent = payload.to_intent()
        await self.execute(intent)

    @abstractmethod
    async def execute(self, intent: "Intent"):
        """
        执行意图（子类必须实现）

        处理接收到的 Intent，进行实际的渲染或输出操作。

        Args:
            intent: 意图对象
        """
        pass

    async def stop(self):
        """停止 Provider"""
        if not self.is_started:
            return

        if self.event_bus:
            self.event_bus.off(CoreEvents.OUTPUT_INTENT_READY, self._on_intent)

        await self.cleanup()
        self.is_started = False

    async def cleanup(self):  # noqa: B027
        """
        清理资源（子类可重写）

        执行清理逻辑，如关闭连接、释放资源等。
        在 stop() 方法中调用。
        """
        pass

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "is_started": self.is_started,
            "type": "output_provider",
            "priority": self.priority,
        }
