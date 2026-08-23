"""
Amaidesu Collectors（采集器）模块（Wave 3 新增模块）

§1.52 顶层定案：
- `modules/collectors/` 框架层第三种角色（与 tools/ 并列）
- 不是工具（非同步/异步二类），是"流型感知者"——世界→系统入口，主动推事件
- 保留"采集器"职责名（用户拍板：设计没变，只是事件名变了；去 `input/` 阶段词）
- 弹幕采集器归位 modules/collectors/bilibili/（W5 才动）

Wave 3 提供：
- ``BaseCollector`` —— 采集器抽象基类（start/stop/cleanup 生命周期）
- ``CollectorManager`` —— 统一管理采集器生命周期（start/stop/健康）
"""

from src.modules.collectors.base import BaseCollector
from src.modules.collectors.manager import CollectorManager

__all__ = [
    "BaseCollector",
    "CollectorManager",
]
