"""
v2 语义域事件 Payload 定义：live 场次生命周期

定义 ``live.started`` / ``live.ended`` 事件 Payload（场次建行 / 下播）。
对应存储 ``live_sessions`` 表（见 .omo/drafts/amaidesu-v2-storage-schema.md）。

按契约（.omo/drafts/amaidesu-v2-event-contract.md "live.*" 节）：
- ``live.started`` Payload 包含 ``started_at_ms``，标识开播时刻
- ``live.ended`` Payload 包含 ``ended_at_ms``，标识下播时刻

设计要点：
- 同一 ``LivePayload`` 类在两个事件名下复用（启动 / 关闭都用同一形状，
  通过 ``started_at_ms`` / ``ended_at_ms`` 是否为 None 区分语义）。
- ``@register_event`` 装饰器是幂等的，相同类登记到第二个事件名不会抛错。
"""

from typing import Optional

from pydantic import Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms


@register_event("live.started")
@register_event("live.ended")
class LivePayload(BasePayload):
    """
    场次生命周期事件 Payload

    事件名：
    - ``live.started`` — 开播（应填充 ``started_at_ms``）
    - ``live.ended`` — 下播（应填充 ``ended_at_ms``）

    发布者：组合根 / 直播接入层
    订阅者：存储（建/更新 ``live_sessions`` 行）、RoomState 记账器
    """

    live_session_id: str = Field(..., description="场次唯一 ID，对应存储 live_sessions 行 PK")
    stream_id: str = Field(..., description="直播流 ID（平台直播间 ID）")
    platform: str = Field(default="", description="直播平台（bili/youtube/twitch 等），空字符串表示未知")
    started_at_ms: Optional[int] = Field(
        default=None,
        description="开播时刻（Unix 毫秒）。live.started 时必填，live.ended 时可选（通常由存储兜底补齐）",
    )
    ended_at_ms: Optional[int] = Field(
        default=None,
        description="下播时刻（Unix 毫秒）。live.ended 时必填，live.started 时为 None",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒），用于日志/排序，与 started_at/ended_at 解耦",
    )


__all__ = ["LivePayload"]
