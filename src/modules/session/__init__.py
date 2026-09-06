"""直播场次管理模块。

场次 = 一段有开始/结束边界的直播时间段；``LiveSessionManager`` 是场次
唯一事实源（开启/结束/删除/归属解析/防膨胀），生命周期广播
``live.started`` / ``live.ended`` 事件。
"""

from .manager import LiveSessionManager, SCRATCH_STREAM_ID

__all__ = ["LiveSessionManager", "SCRATCH_STREAM_ID"]
