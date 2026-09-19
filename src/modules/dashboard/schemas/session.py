"""直播场次 API Schema

定义场次列表、生命周期控制与时间线回看端点的请求/响应模型。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SessionItem(BaseModel):
    """场次列表条目"""

    live_session_id: int = Field(..., description="场次主键（live_sessions.id）")
    source: str = Field(default="manual", description="场次来源：manual / replay / legacy")
    title: Optional[str] = Field(default=None, description="场次标题")
    room_id: str = Field(default="", description="房间/频道标识（普通属性）")
    platform: str = Field(default="", description="平台标识")
    started_at_ms: int = Field(..., description="开始时刻（Unix 毫秒）")
    ended_at_ms: Optional[int] = Field(default=None, description="结束时刻（Unix 毫秒）；NULL=进行中")
    message_count: int = Field(default=0, description="本场次消息数（live_chat 行数）")
    is_active: bool = Field(default=False, description="是否为当前进行中的显式场次")


class SessionListResponse(BaseModel):
    """场次列表响应"""

    items: List[SessionItem] = Field(default_factory=list)
    active_session_id: Optional[int] = Field(default=None, description="当前进行中的显式场次主键")


class SessionOpenRequest(BaseModel):
    """开启场次请求"""

    title: Optional[str] = Field(default=None, description="场次标题（可选）")
    room_id: Optional[str] = Field(default=None, description="房间/频道标识（可选，缺省用装配默认值）")
    platform: Optional[str] = Field(default=None, description="平台标识（可选，缺省用装配默认值）")


class SessionOpenResponse(BaseModel):
    """开启场次响应"""

    live_session_id: int = Field(..., description="新场次主键")


class SessionActionResponse(BaseModel):
    """场次动作通用响应"""

    success: bool = Field(default=True, description="动作是否成功")
    detail: str = Field(default="", description="动作结果说明")


class SessionTimelineResponse(BaseModel):
    """单场时间线回看响应。

    ``items`` 条目形状与实时视图（WS 推送）共用：明细行（消息/发言/礼物/SC）
    与事件条目（``kind="event"``）按 ``ts_ms`` 升序合并。
    """

    live_session_id: int = Field(..., description="场次主键")
    items: List[Dict[str, Any]] = Field(default_factory=list, description="时间线条目（按 ts_ms 升序）")
