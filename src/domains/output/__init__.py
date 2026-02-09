"""
Output Domain - 输出域

负责将RenderParameters渲染到各种输出设备。
包含参数生成和渲染呈现的所有Provider。
"""

# 导出
__all__ = [
    "OutputCoordinator",
    "OutputProviderManager",
]

# 延迟导入以避免循环导入
# 注意：这些导入在类型检查时可用，运行时通过属性访问延迟导入
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.domains.output.coordinator import OutputCoordinator
    from src.domains.output.provider_manager import OutputProviderManager
else:
    # 运行时使用延迟导入函数
    def __getattr__(name: str):
        if name == "OutputCoordinator":
            from src.domains.output.coordinator import OutputCoordinator

            return OutputCoordinator
        elif name == "OutputProviderManager":
            from src.domains.output.provider_manager import OutputProviderManager

            return OutputProviderManager
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
