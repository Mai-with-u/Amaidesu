"""字幕后端集合。

当前内置 Backend：

- ``TkGuiBackend``：包装既有 ``SubtitleGuiService``（长驻 Tk 线程 +
  字幕渲染后端），把 Tool 系统遗留的 GUI 服务接入新 ``SubtitleBackend``
  协议。装配层构造 GUI 服务并启动 Tk 线程后，注入 Backend 实例。
- ``DashboardBackend``：包装 Dashboard 的 ``DanmakuWidgetService`` 字幕
  小部件，把字幕通过 WebSocket（``/ws/subtitle``）推送给 OBS 浏览器源
  等前端展示位。装配层构造 DashboardServer 后，由主组合根注入
  widget_service 引用。
"""

from .dashboard import DashboardBackend
from .tk_gui import TkGuiBackend

__all__ = ["DashboardBackend", "TkGuiBackend"]
