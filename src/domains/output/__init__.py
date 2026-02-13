"""
Output Domain - 输出域

负责将RenderParameters渲染到各种输出设备。
包含参数生成和渲染呈现的所有Provider。

重构后：
- OutputProviderManager 负责数据流协调和 Provider 管理
- 移除了 OutputCoordinator（职责已合并到 OutputProviderManager）
"""

# 导出
__all__ = [
    "OutputProviderManager",
]

# 延迟导入以避免循环导入
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.domains.output.provider_manager import OutputProviderManager
else:
    # 运行时使用延迟导入函数
    def __getattr__(name: str):
        if name == "OutputProviderManager":
            from src.domains.output.provider_manager import OutputProviderManager

            return OutputProviderManager
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
