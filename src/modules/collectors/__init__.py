"""
Amaidesu Collectors（采集器）模块

包定位：
- 持续流数据源（与被动调用的 tools/ 并列）：世界 → 系统入口，主动推事件
- 弹幕采集器位于 modules/collectors/bilibili/

提供：
- ``BaseCollector`` —— 采集器抽象基类（start/stop/cleanup 生命周期）
- ``CollectorManager`` —— 统一管理采集器生命周期（start/stop/健康）
"""

from src.modules.collectors.base import BaseCollector
from src.modules.collectors.manager import CollectorManager

__all__ = [
    "BaseCollector",
    "CollectorManager",
]
