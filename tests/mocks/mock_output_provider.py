"""
Mock 输出 Provider（用于测试）
"""

from typing import Dict, Any, Optional, List
from src.core.base.output_provider import OutputProvider
from src.layers.parameters.render_parameters import RenderParameters


class MockOutputProvider(OutputProvider):
    """Mock 输出 Provider（用于测试）"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config or {})
        self.received_parameters: List[RenderParameters] = []  # 记录收到的参数

    async def render(self, parameters: RenderParameters) -> bool:
        """渲染（记录参数）"""
        self.received_parameters.append(parameters)
        return True

    def get_last_parameters(self) -> Optional[RenderParameters]:
        """获取最后一次收到的参数"""
        return self.received_parameters[-1] if self.received_parameters else None

    def get_all_parameters(self) -> List[RenderParameters]:
        """获取所有收到的参数"""
        return self.received_parameters.copy()

    def clear(self):
        """清空记录"""
        self.received_parameters.clear()
