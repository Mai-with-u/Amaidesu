"""
事件 Payload 定义：live.* 直播场次生命周期

定义 ``live.started`` / ``live.ended`` 场次生命周期事件 Payload。

场次 = 一段有开始/结束边界的直播时间段（LiveSessionManager 维护，
一房多场：房间是场次之上的静态属性）。由 LiveSessionManager 作为
唯一发布方：

- ``live.started``：显式场次开启（手动开启 / 模拟器回放自动开启）。
  默认场次不复用：本事件只在显式 ``open_session`` 开启时发布一次。
- ``live.ended``：显式场次结束（手动结束 / 进程退出收口 / 回放结束）。

字段约束：
- ``live_session_id`` 为 ``live_sessions`` 表 INTEGER 主键，事件即事实：
  订阅方无需再向存储反查。
- ``source`` 标记场次来源（manual=手动 / replay=模拟器回放 / legacy=历史遗留）。
- 时间字段统一毫秒（``timestamp_ms`` / ``started_at_ms`` / ``ended_at_ms``）。
"""

from typing import Optional

from pydantic import Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms


@register_event("live.started")
class LiveStartedPayload(BasePayload):
    """
    直播场次开始事件 Payload

    事件名：``live.started``
    发布者：LiveSessionManager（``open_session``）
    订阅者：观察器（场次侧边栏/状态条）、事件历史（场次边界回看）

    Attributes:
        live_session_id: 场次主键（live_sessions.id）
        source: 场次来源（manual / replay / legacy）
        title: 场次标题（可选，手动开启时可指定）
        room_id: 房间/频道标识（普通属性，可为空）
        platform: 平台标识（可为空）
        started_at_ms: 场次开始时刻（Unix 毫秒）
        timestamp_ms: 事件发布时间戳（Unix 毫秒）
    """

    live_session_id: int = Field(..., description="场次主键（live_sessions.id）")
    source: str = Field(default="manual", description="场次来源：manual / replay / legacy")
    title: Optional[str] = Field(default=None, description="场次标题（可选）")
    room_id: str = Field(default="", description="房间/频道标识（普通属性，可为空）")
    platform: str = Field(default="", description="平台标识（可为空）")
    started_at_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="场次开始时刻（Unix 毫秒）",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


@register_event("live.ended")
class LiveEndedPayload(BasePayload):
    """
    直播场次结束事件 Payload

    事件名：``live.ended``
    发布者：LiveSessionManager（``close_session`` / 进程退出收口）
    订阅者：观察器、事件历史

    Attributes:
        live_session_id: 场次主键（live_sessions.id）
        source: 场次来源（manual / replay / legacy）
        reason: 结束原因说明（手动结束 / 进程退出 / 回放结束等，人类可读）
        duration_ms: 场次时长（毫秒；无法确定时为 None）
        empty_discarded: 空场次是否被丢弃（结束时无任何明细行的场次不保留，
            该标记为 True 时本场次行已被删除，live_session_id 不再有效）
        ended_at_ms: 场次结束时刻（Unix 毫秒）
        timestamp_ms: 事件发布时间戳（Unix 毫秒）
    """

    live_session_id: int = Field(..., description="场次主键（live_sessions.id）")
    source: str = Field(default="manual", description="场次来源：manual / replay / legacy")
    reason: str = Field(default="", description="结束原因说明（人类可读）")
    duration_ms: Optional[int] = Field(default=None, ge=0, description="场次时长（毫秒）")
    empty_discarded: bool = Field(
        default=False,
        description="空场次丢弃标记：True 时场次行已删除，live_session_id 不再有效",
    )
    ended_at_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="场次结束时刻（Unix 毫秒）",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


__all__ = ["LiveEndedPayload", "LiveStartedPayload"]
