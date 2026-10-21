"""直播场次 API Schema

定义场次列表、生命周期控制与时间线回看端点的请求/响应模型。
"""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


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


class SessionRenameRequest(BaseModel):
    """场次重命名请求"""

    title: Optional[str] = Field(default=None, description="新标题；None/空串=清除标题")


class SessionTimelineResponse(BaseModel):
    """单场时间线回看响应。

    ``items`` 条目形状与实时视图（WS 推送）共用：明细行（消息/发言/礼物/SC）
    与事件条目（``kind="event"``，事件侧覆盖环形缓冲与诊断文件两来源）按
    ``ts_ms`` 升序合并；游标分页由 ``before_timestamp_ms`` 请求参数驱动，
    ``has_more`` 指示是否还有更早条目。
    """

    live_session_id: int = Field(..., description="场次主键")
    items: List["SessionTimelineItem"] = Field(default_factory=list, description="时间线条目（按 ts_ms 升序）")
    has_more: bool = Field(default=False, description="是否还有更早条目（以最旧条目 ts_ms 为游标续翻）")
    events_pruned: bool = Field(
        default=False,
        description="场次时间范围内存在缺失的诊断天目录（被清理或未落盘），事件侧降级为空/部分",
    )


class _TimelineEntry(BaseModel):
    """时间线条目公共约束：形状互斥靠 extra=forbid + kind 字面量保证。"""

    model_config = ConfigDict(extra="forbid")

    kind: str
    ts_ms: int = Field(description="条目时刻（Unix 毫秒）")


class TimelineMessageItem(_TimelineEntry):
    """观众消息明细行（live_chat 行，kind 为 message_type 直通：danmaku/guard 等）。"""

    user_name: str = ""
    user_id: str = ""
    content: str
    message_id: Optional[str] = None
    simulated: bool = False


class TimelineSpeechItem(_TimelineEntry):
    """主播发言明细行（kind 固定 speech）。"""

    kind: Literal["speech"]
    text: str
    reply_to_message_id: Optional[str] = None
    simulated: bool = False


class TimelineGiftItem(_TimelineEntry):
    """礼物明细行（gift_row 经 kind 映射后的条目）。"""

    kind: Literal["gift"]
    user_name: str
    user_id: str
    gift_name: str
    gift_count: int
    # 仓储行自带总价（贝壳*数量），声明以通过 extra=forbid 的响应校验
    total_price: int = 0
    simulated: bool = False


class TimelineSuperChatItem(_TimelineEntry):
    """SC 明细行（super_chat_row 经 kind 映射后的条目）。"""

    kind: Literal["super_chat"]
    user_name: str
    user_id: str
    content: str
    amount: float
    simulated: bool = False


class TimelineEventItem(_TimelineEntry):
    """事件历史条目（工具/决策/裁决/阶段/游戏/递话/响应等，与实时卡面同源）。

    事件侧事实源为诊断事件流文件（logs/events）∪ 内存环形缓冲；诊断天目录
    被清理后相应时段事件条目缺失（``events_pruned`` 如实回报）。
    """

    kind: Literal["event"]
    event_type: str
    data: Dict[str, Any] = Field(default_factory=dict)


# 条目联合：明细行 kinds（danmaku/guard 等动态值）由消息条目兜底，
# speech/gift/super_chat/event 的 kind 字面量与 extra=forbid 保证互斥匹配
SessionTimelineItem = Union[
    TimelineSpeechItem,
    TimelineGiftItem,
    TimelineSuperChatItem,
    TimelineEventItem,
    TimelineMessageItem,
]


# 前向引用在此刻已全部可解析，显式重建避免延迟解析失败
SessionTimelineResponse.model_rebuild()


class RundownSegmentRun(BaseModel):
    """流程单单段执行事实（一次进入 = 一行；goto 回跳会再次出现）。

    停留口径：墙钟时长（含暂停），``pause_ms`` 单列供扣除；``left_at_ms``
    为空表示直到场次结束/当前时刻仍停留在该环节（停留未封口，``dwell_ms``
    仅含已确认部分——进行中场次恒为 0）。
    """

    seg_id: str
    seg_title: str = ""
    seg_index: int = 0
    entered_at_ms: int
    left_at_ms: Optional[int] = None
    dwell_ms: int = 0
    expected_ms: Optional[int] = None
    """进入时该环节的预计时长（变更事实行自带，模板改写后以最新一次为准）。"""
    pause_ms: int = 0


class SessionRundownExecutionResponse(BaseModel):
    """单场流程单执行回看（``GET /live-sessions/{id}/rundown-execution``）。

    把 rundown_transitions 事实行重放为"各环节实际停留 vs 预计时长"的
    结构化视图；无任何变更事实的场次 segments 为空。
    """

    live_session_id: int
    rundown_id: str = ""
    segments: List[RundownSegmentRun] = Field(default_factory=list)
    pause_total_ms: int = 0
    transitions_count: int = 0
