"""观众域请求/响应契约

观众列表、互动分析、单观众档案、对话批次、贡献明细、参与场次与身份人工
处理的全部请求/响应模型。字段即 JSON 形状，前端按此消费。
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ViewerListItem(BaseModel):
    """单行观众统计（viewers 表行的投影）。"""

    viewer_id: int = Field(description="内部观众 ID（单观众端点按它定位）")
    platform: str
    user_id: str = Field(description="代表外部 ID（账号 ID 优先，其次开放平台 ID；没有真实 ID 时为空）")
    user_name: str
    link_status: str = Field(default="confirmed", description="confirmed=已确认 / pending=待人工确认")
    message_count: int
    gift_count: int
    replied_count: int
    interaction_count: int
    paid_count: int
    paid_amount: int = Field(description="付费总额（金瓜子，展示层 ÷1000 = 元）")
    last_active_ms: int


class ViewerListResponse(BaseModel):
    """观众列表响应：``total`` 为命中搜索条件的全量行数（分页器用）。"""

    total: int
    items: List[ViewerListItem]


class DailyDanmakuPoint(BaseModel):
    """按天弹幕量（本地日期 ``YYYY-MM-DD``）。"""

    day: str
    count: int


class ViewerInsightsResponse(BaseModel):
    """互动分析聚合：活跃分桶互斥（today/week/month/older）。"""

    total_viewers: int
    active_today: int
    active_week: int
    active_month: int
    active_older: int
    never_replied: int
    gift_viewers: int
    daily_danmaku: List[DailyDanmakuPoint]


class ViewerNameItem(BaseModel):
    """观众用过的一个昵称及其最早、最晚出现时刻。"""

    user_name: str
    first_seen_ms: int
    last_seen_ms: int


class ViewerBrief(BaseModel):
    """另一位观众的简要信息（待确认的并入建议等）。"""

    viewer_id: int
    user_name: str


class ViewerDetailResponse(BaseModel):
    """单观众档案：viewers 观众行 + 用过的昵称 + 明细表聚合。"""

    viewer_id: int
    platform: str
    user_id: str = Field(description="代表外部 ID（账号 ID 优先）")
    account_id: Optional[str] = Field(default=None, description="平台账号 ID（B 站 uid）")
    open_id: Optional[str] = Field(default=None, description="开放平台 ID（B 站 open_id）")
    open_app_id: Optional[str] = Field(default=None, description="发出 open_id 的开放平台应用；空串 = 存量数据不明")
    user_name: str
    names: List[ViewerNameItem] = Field(default_factory=list, description="用过的昵称（含当前昵称），最近在前")
    link_status: str = Field(default="confirmed", description="confirmed=已确认 / pending=待人工确认")
    link_candidate: Optional[ViewerBrief] = Field(default=None, description="待确认时建议并入的老观众")
    message_count: int
    gift_count: int
    replied_count: int
    interaction_count: int
    paid_count: int
    paid_amount: int = Field(default=0, description="付费总额（金瓜子）")
    last_active_ms: int
    first_seen_ms: Optional[int] = Field(default=None, description="明细表中最早出现时刻；无明细为 NULL")
    gift_total_count: int = Field(default=0, description="礼物总件数")
    gift_total_amount: int = Field(default=0, description="礼物标价总额（金瓜子）")
    sc_total_amount: float = Field(default=0.0, description="SC 总金额（元，金瓜子 ÷1000）")
    sc_total_count: int = Field(default=0, description="SC 条数")
    session_count: int = Field(default=0, description="参与过的直播场次数")


class DialogueItem(BaseModel):
    """对话交织行：观众消息（viewer）或主播对其的回复（reply）。"""

    id: int = Field(..., description="live_chat 行主键，前端跨批去重用")
    kind: str = Field(..., description="viewer=观众消息 / reply=主播回复")
    content: str
    timestamp_ms: int
    live_session_id: Optional[int] = None
    message_type: Optional[str] = Field(default=None, description="viewer 行的消息类型（danmaku/guard/enter）")
    message_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None
    simulated: bool = False


class DialogueResponse(BaseModel):
    """对话批次响应：``next_before`` 为下一批游标（观众消息主轴），无更多为 NULL。"""

    items: List[DialogueItem]
    next_before: Optional[int] = None


class GiftItem(BaseModel):
    """礼物明细行。"""

    timestamp_ms: int
    live_session_id: Optional[int] = None
    gift_name: str
    gift_count: int
    total_price: int = Field(default=0, description="标价总额（金瓜子）")
    simulated: bool = False


class SuperChatItem(BaseModel):
    """SC 明细行。"""

    timestamp_ms: int
    live_session_id: Optional[int] = None
    amount: float
    message: str
    simulated: bool = False


class ContributionsResponse(BaseModel):
    """观众贡献：汇总数字 + 礼物与 SC 明细（时间倒序，最新在前）。"""

    gift_total_count: int
    sc_total_amount: float
    sc_total_count: int
    gifts: List[GiftItem]
    super_chats: List[SuperChatItem]


class ViewerSessionItem(BaseModel):
    """观众参与的单个场次聚合行。"""

    live_session_id: int
    title: Optional[str] = None
    message_count: int
    first_ms: int
    last_ms: int


class ViewerSessionsResponse(BaseModel):
    """参与场次列表（最近参与在前）。"""

    items: List[ViewerSessionItem]


class PendingViewerItem(BaseModel):
    """一位待确认观众：新建的这一位 + 建议并入的老观众，供人工比对。"""

    viewer_id: int
    platform: str
    user_name: str
    account_id: Optional[str] = None
    open_id: Optional[str] = None
    message_count: int
    interaction_count: int
    paid_amount: int = Field(default=0, description="付费总额（金瓜子）")
    last_active_ms: int
    candidate: Optional[ViewerBrief] = None
    candidate_account_id: Optional[str] = None
    candidate_open_id: Optional[str] = None
    candidate_name_seen_ms: int = Field(default=0, description="老观众最近一次以该昵称出现的时刻")
    candidate_message_count: int = 0
    candidate_interaction_count: int = 0
    candidate_paid_amount: int = 0
    candidate_last_active_ms: int = 0
    candidate_names: List[str] = Field(default_factory=list, description="老观众用过的昵称（最近在前）")


class PendingViewersResponse(BaseModel):
    """待确认观众列表（最近活跃在前）。"""

    total: int
    items: List[PendingViewerItem]


class MergeRequest(BaseModel):
    """合并请求：把路径里的观众并入 ``target_viewer_id``。"""

    target_viewer_id: int


class UnlinkRequest(BaseModel):
    """取消关联请求：拆出哪一种外部 ID。"""

    id_kind: Literal["account", "open"]


class IdentityActionResponse(BaseModel):
    """人工处理结果：``viewer_id`` 为处理后应跳转查看的观众（合并的目标 / 拆出的新观众 / 原观众）。"""

    viewer_id: int
