"""直播场次管理模块。

场次 = 一段有开始/结束边界的直播时间段；``LiveSessionManager`` 是场次
唯一事实源（开启/结束/删除/归属解析）。生命周期广播 ``live.started`` /
``live.ended`` 事件；``resolve_pk()`` 在无显式进行中场次时返回 ``None``，
下游落库路径据此跳过——消息仅在内存流转，不写入业务明细表。
"""

from .manager import LiveSessionManager

__all__ = ["LiveSessionManager"]
