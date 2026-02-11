"""
AvatarProviderBase - 虚拟形象 Provider 抽象基类

定义了所有 Avatar Provider 的通用处理流程:
1. 订阅 DECISION_INTENT 事件
2. 适配 Intent 为平台参数 (_adapt_intent)
3. 渲染到平台 (_render_internal)
4. 连接/断开管理
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, Optional

from src.modules.logging import get_logger
from src.modules.types.base.output_provider import OutputProvider

if TYPE_CHECKING:
    from src.modules.types import Intent
    from src.modules.events.event_bus import EventBus


class AvatarProviderBase(OutputProvider, ABC):
    """
    虚拟形象 Provider 抽象基类

    提供通用的处理流程，子类只需实现平台特定的适配和渲染逻辑。
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)
        self._is_connected = False
        self._dependencies: Dict[str, Any] = {}

    async def setup(
        self,
        event_bus: "EventBus",
        dependencies: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        设置 Provider

        Args:
            event_bus: EventBus 实例
            dependencies: 可选的依赖注入（替代 core）
        """
        from src.modules.events.names import CoreEvents

        self.event_bus = event_bus
        self._dependencies = dependencies or {}

        # 订阅 DECISION_INTENT 事件
        event_bus.on(CoreEvents.DECISION_INTENT, self._on_intent_ready)

        # 连接平台
        await self._connect()

        self.logger.info(f"{self.__class__.__name__} 已启动")

    async def cleanup(self):
        """清理资源（通用逻辑）"""
        from src.modules.events.names import CoreEvents

        if self.event_bus:
            self.event_bus.off(CoreEvents.DECISION_INTENT, self._on_intent_ready)

        # 断开连接
        await self._disconnect()

        self.logger.info(f"{self.__class__.__name__} 已清理")

    async def _on_intent_ready(self, event_name: str, payload: Any, source: str):
        """
        处理渲染意图（通用流程）

        Args:
            intent: 平台无关的 Intent
        """
        from src.modules.events.payloads.decision import IntentPayload

        if isinstance(payload, IntentPayload):
            intent = payload.to_intent()
        else:
            return

        if not self._is_connected:
            self.logger.warning("未连接，跳过渲染")
            return

        try:
            # ✅ 适配 Intent 为平台参数（子类实现）
            params = self._adapt_intent(intent)

            # ✅ 渲染到平台（子类实现）
            await self._render_internal(params)

        except Exception as e:
            self.logger.error(f"渲染失败: {e}", exc_info=True)

    # ==================== 子类必须实现的抽象方法 ====================

    @abstractmethod
    def _adapt_intent(self, intent: "Intent") -> Any:
        """
        适配 Intent 为平台特定参数

        子类必须实现此方法，直接在内部完成 Intent → 平台参数的转换。
        可以使用类变量 EMOTION_MAP 和 ACTION_MAP 存储映射关系。

        Returns:
            平台特定的参数对象（可以是 Dict、Pydantic Model 等）
        """
        pass

    @abstractmethod
    async def _render_internal(self, params: Any) -> None:
        """
        平台特定的渲染逻辑

        Args:
            params: _adapt_intent() 返回的平台特定参数
        """
        pass

    @abstractmethod
    async def _connect(self) -> None:
        """连接到平台"""
        pass

    @abstractmethod
    async def _disconnect(self) -> None:
        """断开平台连接"""
        pass
