"""
Avatar Output Provider - Output Domain: 虚拟形象输出

职责:
- 将 ExpressionParameters 渲染到虚拟形象平台
- 使用 PlatformAdapter 执行渲染
- 支持多个平台（VTS、VRChat、Live2D）
"""

from typing import Optional, Dict, Any

from src.core.base.output_provider import OutputProvider
from src.core.base.base import RenderParameters
from src.domains.output.adapters import PlatformAdapter
from src.domains.output.adapter_factory import AdapterFactory
from src.core.utils.logger import get_logger


class AvatarOutputProvider(OutputProvider):
    """虚拟形象输出 Provider

    使用 PlatformAdapter 执行渲染，支持多个平台。
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger("AvatarOutputProvider")

        self.adapter: Optional[PlatformAdapter] = None
        self.adapter_type = config.get("adapter_type", "vts")

    async def _setup_internal(self):
        """初始化适配器"""
        self.logger.info(f"初始化 AvatarOutputProvider，平台类型: {self.adapter_type}")

        # 创建适配器
        self.adapter = AdapterFactory.create(self.adapter_type, self.config)
        if not self.adapter:
            raise RuntimeError(f"无法创建 {self.adapter_type} 适配器")

        # 连接平台
        connected = await self.adapter.connect()
        if not connected:
            raise RuntimeError(f"{self.adapter_type} 适配器连接失败")

        self.logger.info("AvatarOutputProvider 初始化完成")

    async def _render_internal(self, parameters: RenderParameters):
        """渲染表情参数

        Args:
            parameters: 渲染参数（包含 expressions 字段）
        """
        if not self.adapter or not self.adapter.is_connected:
            self.logger.warning("适配器未连接，跳过渲染")
            return

        try:
            # 使用抽象参数，适配器负责翻译
            if parameters.expressions_enabled and parameters.expressions:
                await self.adapter.set_parameters(parameters.expressions)
                self.logger.debug(f"表情参数已设置: {parameters.expressions}")
        except Exception as e:
            self.logger.error(f"渲染表情参数失败: {e}", exc_info=True)

    async def _cleanup_internal(self):
        """清理适配器连接"""
        self.logger.info("清理 AvatarOutputProvider...")

        if self.adapter:
            await self.adapter.disconnect()
            self.adapter = None

        self.logger.info("AvatarOutputProvider 清理完成")

    def get_info(self) -> Dict[str, Any]:
        """获取 Provider 信息"""
        info = super().get_info()
        info.update(
            {
                "adapter_type": self.adapter_type,
                "is_connected": self.adapter.is_connected if self.adapter else False,
            }
        )
        return info
