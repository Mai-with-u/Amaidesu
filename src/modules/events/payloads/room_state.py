"""
事件 Payload 定义：room.state.* 直播间状态快照

状态子层（当前属性快照）与行为流（``room.py`` 的 ``room.message.*``，发生
的事）分属不同子层、禁止平铺同层。此处事件无发送者、无消息内容——值即
当前状态，变化即重发。

契约约定：
- 不落 ``live_chat`` 等明细表（明细表是行为流事实源）；状态由订阅方
  （RoomState）承接为内存快照，经后台记账写入 ``live_sessions``
- 时间字段统一毫秒（``timestamp_ms``）
"""

from pydantic import ConfigDict, Field

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms


@register_event(CoreEvents.ROOM_STATE_WATCHED_COUNT)
class RoomStateWatchedPayload(BasePayload):
    """
    累计观看人次状态快照（``room.state.watched_count``）

    数据源：B 站 open-live ``LIVE_OPEN_PLATFORM_WATCHED_CHANGE`` 推送——
    ``watched_count`` 为本场累计观看人次（UV 口径，只增不减），变化即推。
    注意：open-live 协议无"当前在线人数"推送，累计口径是与协议唯一对得上
    的真值；``live_sessions.viewer_count``（当前在线）暂无数据源，维持 0。

    发布者：直播接入层（B 站采集器，platform 为装配期常量）
    订阅者：主播侧 RoomState（更新内存快照，供后台记账写 live_sessions）

    Attributes:
        platform: 平台标识（采集器作为装配期常量统一注入；空串表示未归属平台）
        audience_total: 本场累计观看人次（UV 口径；映射 live_sessions.audience_total）
        watched_show: 平台人读展示文案（如 "1.2万人看过"；仅供展示，数值以
            audience_total 为准）
        timestamp_ms: 事件时间戳（Unix 毫秒；推送无时间字段，取接收时刻）
        simulated: 数据溯源标记：True=模拟/回放源，统计与入库需过滤
    """

    platform: str = Field(
        default="",
        description="平台标识（采集器装配期统一注入；bilibili 等）",
    )
    audience_total: int = Field(
        default=0,
        ge=0,
        description="本场累计观看人次（UV 口径，只增不减；映射 live_sessions.audience_total）",
    )
    watched_show: str = Field(
        default="",
        description="平台人读展示文案（如 1.2万人看过）；数值口径以 audience_total 为准",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件时间戳（Unix 毫秒；推送无时间字段，取接收时刻）",
    )
    simulated: bool = Field(
        default=False,
        description="数据溯源标记：True=模拟/回放源，统计与入库需过滤",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "platform": "bilibili",
                "audience_total": 12000,
                "watched_show": "1.2万人看过",
                "timestamp_ms": 1706745600000,
            }
        }
    )


__all__ = ["RoomStateWatchedPayload"]
