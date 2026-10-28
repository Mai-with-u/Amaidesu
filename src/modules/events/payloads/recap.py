"""
事件 Payload 定义：streamer.recap.written

定义 ``streamer.recap.written`` 场次回顾写好事件 Payload。

收播整理（``live.ended`` 后的后台任务）或启动补写把一场直播总结成回顾并写入
``session_recaps`` 后发一条。回顾由模型异步生成，写完的时刻只有写入方知道；
需要"上一场播了什么"的外部消费者（如向 QQ 那边的麦麦同步近况）订阅本事件即可，
不必反查存储或猜测写入时机。

字段约束：
- ``live_session_id``：回顾所属场次主键（发布方显式填写；补写的是过去的场次，
  不能交给场次盖章拦截器按"当前场次"注入）。
- ``kind``：回顾所属场次的类型（live=正式 / rehearsal=彩排），取自场次行；
  对外同步只推正式场。
- 时间字段统一毫秒。
"""

from pydantic import Field

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.types.session_kind import SESSION_KIND_LIVE, SessionKind
from src.modules.utils.time_utils import now_ms


@register_event(CoreEvents.STREAMER_RECAP_WRITTEN)
class StreamerRecapWrittenPayload(BasePayload):
    """
    场次回顾写好事件 Payload

    事件名：``streamer.recap.written``
    发布者：``SessionRecapWriter``（收播整理与启动补写共用的写入边界）
    订阅者：直播近况同步（``maibot_sync``）

    Attributes:
        live_session_id: 回顾所属场次主键（live_sessions.id）
        kind: 回顾所属场次的类型（live=正式 / rehearsal=彩排）
        started_at_ms: 场次开始时刻（Unix 毫秒）
        ended_at_ms: 场次结束时刻（Unix 毫秒）
        recap_text: 回顾正文（已按上限截断）
        timestamp_ms: 事件发布时间戳（Unix 毫秒）
    """

    live_session_id: int = Field(..., gt=0, description="回顾所属场次主键（live_sessions.id）")
    kind: SessionKind = Field(default=SESSION_KIND_LIVE, description="回顾所属场次的类型：live=正式 / rehearsal=彩排")
    started_at_ms: int = Field(..., description="场次开始时刻（Unix 毫秒）")
    ended_at_ms: int = Field(..., description="场次结束时刻（Unix 毫秒）")
    recap_text: str = Field(..., min_length=1, description="回顾正文（已按上限截断）")
    timestamp_ms: int = Field(default_factory=lambda: now_ms(), description="事件发布时间戳（Unix 毫秒）")


__all__ = [
    "StreamerRecapWrittenPayload",
]
