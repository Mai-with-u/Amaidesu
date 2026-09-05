"""DashboardBackend - 包装 Dashboard 弹幕小部件服务的字幕 Backend

包装 Dashboard 服务器持有的 ``DanmakuWidgetService`` 字幕小部件，
把字幕基础设施 ``SubtitleService`` 的 ``show`` / ``clear`` 调用转发
到 widget 的 ``show_subtitle`` / ``clear_subtitle`` 公开方法，最终通
过 WebSocket（``/ws/subtitle``）推送给 OBS 浏览器源等前端展示位。

历史背景：

v2 早期 Dashboard 字幕小部件订阅 ``planner.checkpoint`` 业务事件
拉字幕——但 ``CheckpointPayload`` 无 ``speech`` 字段，handler 永远
``return``，字幕静默失效。字幕升级为基础设施后，本 Backend 把
widget 暴露的 ``show_subtitle`` / ``clear_subtitle`` 接入新
``SubtitleBackend`` 协议——内容源从 ``planner.checkpoint`` 拉取改为
``SubtitleService.show`` 主动广播（由编排层订阅 ``streamer.speech``
驱动）。

职责：

- ``show`` → 转发到 ``DanmakuWidgetService.show_subtitle(text)``；
  ``utterance_id`` 暂不传给 widget（widget 暂无该键的展示位）
- ``clear`` → 转发到 ``DanmakuWidgetService.clear_subtitle()``
- ``enabled`` → 转发 ``widget.subtitle_config.enabled``（widget 关
  闭时本 Backend 不产生副作用）

生命周期：

- Backend **不**实例化 widget——装配层在构造 ``DashboardServer``
  并 ``start()`` 后，由 widget 自身持有 ``subtitle_callback`` /
  ``broadcast_callback`` 注册到内部，本 Backend 仅通过 ``widget_service``
  引用调用其公开方法
- Backend 自身无 ``setup`` / ``cleanup``；本协议未要求生命周期方法
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.widget.service import DanmakuWidgetService


class DashboardBackend:
    """Dashboard 字幕 Backend，包装 ``DanmakuWidgetService`` 字幕小部件。

    满足 ``SubtitleBackend`` 协议（结构类型，不显式继承）：

    - ``async def show(text, utterance_id=None)`` → 转发 widget.show_subtitle
    - ``async def clear()`` → 转发 widget.clear_subtitle
    - ``enabled`` 属性 → 转发 widget.subtitle_config.enabled
    """

    def __init__(self, widget_service: "DanmakuWidgetService") -> None:
        """组合引用外部注入的 ``DanmakuWidgetService`` 实例。

        Args:
            widget_service: 由 Dashboard server 持有并已启动的弹幕小部
                件服务实例。构造 Backend 不触发 widget 生命周期方法，
                不修改 widget 任何状态。
        """
        self._widget: "DanmakuWidgetService" = widget_service
        self.logger = get_logger(self.__class__.__name__)

    @property
    def enabled(self) -> bool:
        """Backend 是否启用。

        转发 widget 的 ``subtitle_config.enabled``——widget 关闭时
        ``show`` / ``clear`` 调用仅清空队列不广播，前端无展示意义。
        """
        return bool(self._widget is not None and self._widget.subtitle_config.enabled)

    async def show(self, text: str, utterance_id: Optional[str] = None) -> None:
        """推送字幕文本——转发到 widget 公开方法。

        ``utterance_id`` 暂不传给 widget（widget 暂无该键的展示位，
        留作未来在 Dashboard 上做关联展示如"发言 N"标记时再用）。
        ``DanmakuWidgetService.show_subtitle`` 自身在 ``subtitle_config.enabled``
        为 False 时早退，本 Backend 不重复 gate。
        """
        await self._widget.show_subtitle(text)

    async def clear(self) -> None:
        """清空字幕显示——转发到 widget 公开方法。

        调用 ``DanmakuWidgetService.clear_subtitle()``，由 widget
        清空队列并广播清空消息。
        """
        await self._widget.clear_subtitle()


__all__ = ["DashboardBackend"]
