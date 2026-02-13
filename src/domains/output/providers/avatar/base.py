"""
AvatarProviderBase - 虚拟形象 Provider 抽象基类

定义了所有 Avatar Provider 的通用处理流程:
1. 继承 OutputProvider，自动订阅 OUTPUT_INTENT 事件
2. 适配 Intent 为平台参数 (_adapt_intent)
3. 渲染到平台 (_render_to_platform)
4. 连接/断开管理
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from src.modules.logging import get_logger
from src.modules.types.base.output_provider import OutputProvider

if TYPE_CHECKING:
    from src.modules.types import Intent


class AvatarProviderBase(OutputProvider, ABC):
    """
    虚拟形象 Provider 抽象基类（重构后）

    继承 OutputProvider，自动订阅 OUTPUT_INTENT 事件。
    子类只需实现平台特定的适配和渲染逻辑。
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)
        self._is_connected = False

    async def execute(self, intent: "Intent"):
        """
        执行意图，适配后渲染到平台

        Args:
            intent: 平台无关的 Intent
        """
        if not self._is_connected:
            self.logger.warning("未连接，跳过渲染")
            return

        try:
            # 适配 Intent 为平台参数
            params = self._adapt_intent(intent)
            # 渲染到平台
            await self._render_to_platform(params)
        except Exception as e:
            self.logger.error(f"渲染失败: {e}", exc_info=True)

    # ==================== 子类必须实现的抽象方法 ====================

    @abstractmethod
    def _adapt_intent(self, intent: "Intent") -> Any:
        """
        适配 Intent 为平台特定参数

        子类必须实现此方法，直接在内部完成 Intent → 平台参数的转换。

        Returns:
            平台特定的参数对象（可以是 Dict、Pydantic Model 等）
        """
        pass

    @abstractmethod
    async def _render_to_platform(self, params: Any) -> None:
        """
        平台特定的渲染逻辑

        Args:
            params: _adapt_intent() 返回的平台特定参数
        """
        pass

    # ==================== 生命周期方法 ====================

    async def init(self):
        """初始化 Provider：连接平台"""
        await self._connect()
        self.logger.info(f"{self.__class__.__name__} 已启动")

    async def cleanup(self):
        """清理资源：断开连接"""
        await self._disconnect()
        self.logger.info(f"{self.__class__.__name__} 已停止")

    @abstractmethod
    async def _connect(self) -> None:
        """连接到平台"""
        pass

    @abstractmethod
    async def _disconnect(self) -> None:
        """断开平台连接"""
        pass
