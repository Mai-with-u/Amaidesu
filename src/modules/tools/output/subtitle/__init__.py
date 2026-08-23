"""字幕工具模块（Wave 4 拆分）

将旧 ``SubtitleHandler`` 拆为两部分：
- ``SubtitleGuiService``：长驻 Tk 线程 + 字幕渲染后端（由 main.py 启动）
- ``SubtitleProvider``：ToolProvider，暴露 ``push_subtitle`` 工具

迁移策略（与 .omo/drafts/amaidesu-v2-migration.md A 段对齐）:
- 配置字段 verbatim 保留（CONFIG_VERSION 不动）
- 去除 EventBus 订阅 / @handler 装饰器（subtitles 通过 GUI 服务入队渲染）
"""

from .subtitle_provider import (
    SubtitleProvider,
    create_subtitle_provider,
    register_subtitle_tools,
)
from .subtitle_service import SubtitleGuiService

__all__ = [
    "SubtitleProvider",
    "SubtitleGuiService",
    "create_subtitle_provider",
    "register_subtitle_tools",
]
