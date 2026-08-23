"""Warudo 字幕系统（迁移至 modules/tools/output）"""

from .subtitle_manager import WarudoSubtitleManager
from .templates import render_subtitle_html

__all__ = ["WarudoSubtitleManager", "render_subtitle_html"]
