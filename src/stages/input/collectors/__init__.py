"""
Input Collectors - 旧路径 Collector 注册入口（Wave 5 迁移后）

W5 已迁移至 ``src/modules/collectors/``（base.py + 各类实现）。
本包仅保留 ``text_adv_game``（未在 W5 范围，W7 才处理）。

新代码请直接 ``from src.modules.collectors.xxx import YyyCollector``。
"""

from .text_adv_game import TextAdvGameCollector

__all__ = ["TextAdvGameCollector"]
