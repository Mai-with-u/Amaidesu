"""
Mock 输出 Provider（用于测试）
"""

from typing import Any

from src.domains.output.parameters.render_parameters import RenderParameters
from src.modules.types.base.output_provider import OutputProvider


class MockOutputProvider(OutputProvider):
    """Mock 输出 Provider（用于测试）"""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config or {})
        self.received_parameters: list[RenderParameters] = []  # 记录收到的参数

    async def _render_internal(self, parameters: RenderParameters) -> bool:
        """渲染（记录参数）"""
        self.received_parameters.append(parameters)
        return True

    def get_last_parameters(self) -> RenderParameters | None:
        """获取最后一次收到的参数"""
        return self.received_parameters[-1] if self.received_parameters else None

    def get_all_parameters(self) -> list[RenderParameters]:
        """获取所有收到的参数"""
        return self.received_parameters.copy()

    def clear(self):
        """清空记录"""
        self.received_parameters.clear()

    async def cleanup(self):
        """清理方法（兼容性）"""
        await self.stop()
