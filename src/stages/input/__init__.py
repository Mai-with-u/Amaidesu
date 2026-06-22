"""
Input 阶段 - 输入阶段

包含数据采集和消息标准化的实现。
负责将外部数据转换为NormalizedMessage。
"""

from .manager import InputCollectorManager

__all__ = ["InputCollectorManager"]
