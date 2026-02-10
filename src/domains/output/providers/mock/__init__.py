"""Mock Output Provider - 用于测试"""

from typing import Literal

from pydantic import Field

from src.domains.output.parameters.render_parameters import RenderParameters
from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
from src.modules.registry import ProviderRegistry
from src.modules.types.base.output_provider import OutputProvider


class MockOutputProvider(OutputProvider):
    """
    模拟输出Provider（用于测试）

    记录收到的所有 RenderParameters，不进行实际渲染。
    """

    class ConfigSchema(BaseProviderConfig):
        """模拟输出Provider配置（用于测试）"""

        type: Literal["mock"] = "mock"
        log_received: bool = Field(default=True, description="是否记录收到的参数")

    def __init__(self, config: dict):
        self.config = config
        self.logger = get_logger("MockOutputProvider")
        self.received_params = []

        self.logger.info("MockOutputProvider初始化完成")

    async def _render_internal(self, params: RenderParameters) -> None:
        """记录收到的参数（内部渲染逻辑）"""
        self.received_params.append(params)
        self.logger.debug(f"收到参数: {params.expression}, text={params.text[:50] if params.text else ''}...")

    async def _setup_internal(self) -> None:
        """设置Provider（内部设置逻辑）"""
        self.logger.info("MockOutputProvider设置完成")

    async def cleanup(self) -> None:
        """清理Provider"""
        self.logger.info(f"MockOutputProvider清理完成，共收到 {len(self.received_params)} 条参数")

    def get_received_params(self):
        """获取收到的所有参数（用于测试断言）"""
        return self.received_params

    def clear_received_params(self):
        """清空记录（用于测试）"""
        self.received_params.clear()


# 注册到 ProviderRegistry
ProviderRegistry.register_output("mock", MockOutputProvider, source="builtin:mock")

__all__ = ["MockOutputProvider"]
