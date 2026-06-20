"""
Output Domain - 输出域

负责将Intent渲染到各种输出设备。
包含参数生成和渲染呈现的所有Handler。
"""

from .manager import OutputHandlerManager

__all__ = [
    "OutputHandlerManager",
]
