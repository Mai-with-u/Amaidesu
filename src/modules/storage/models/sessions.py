"""场次域数据契约（live_sessions 表的读模型）

``live_sessions`` 一行 = 一场直播（有开始/结束边界），主键自增；房间/频道
是普通属性列。本文件是该表读取结果的持久化契约，被主播 Agent（场次回顾）、
session 管理器（残留收口/结账）、Dashboard 场次 API、复盘聚合与
maibot_sync 整场记录共同消费，依赖方向一律向下。

``LiveSessionListItem`` 是列表读面的派生行：在表行之上附加一条消息数
聚合列（子查询 COUNT），只由 ``SessionRepo.list_live_sessions`` 产出。

时间字段统一毫秒（Unix epoch 毫秒）；``ended_at_ms`` 为 ``None`` 即场次
进行中（未结账）。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from src.modules.types.session_kind import SESSION_KIND_LIVE, SessionKind

__all__ = ["LiveSession", "LiveSessionListItem"]


class LiveSession(BaseModel):
    """``live_sessions`` 一行：一场直播的元信息与实时状态。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="场次主键（自增）")
    stream_id: str = Field(default="", description="房间/频道 ID")
    platform: str = Field(default="unknown", description="平台标识")
    started_at_ms: int = Field(..., description="开播时刻（Unix epoch 毫秒）")
    ended_at_ms: Optional[int] = Field(default=None, description="结账时刻（Unix epoch 毫秒）；None 即进行中")
    title: Optional[str] = Field(default=None, description="场次标题；None 即未命名")
    source: str = Field(default="manual", description="场次来源（manual / replay / generate / legacy）")
    kind: SessionKind = Field(default=SESSION_KIND_LIVE, description="场次类型（live=正式 / rehearsal=彩排）")
    heat: int = Field(default=0, description="最近一次心跳的热度")
    viewer_count: int = Field(default=0, description="最近一次心跳的观众数")
    audience_total: int = Field(default=0, description="累计观众数")
    updated_at_ms: int = Field(..., description="行最近更新时刻（Unix epoch 毫秒）")


class LiveSessionListItem(LiveSession):
    """场次列表读面的一行：表行之上附加本场消息数聚合列。"""

    message_count: int = Field(default=0, description="本场 live_chat 行数（列表子查询聚合）")
