"""场次复盘聚合响应模型（``/api/v1/review``）

纯聚合只读面：全部字段来自结构化数据（库表 + LLM 请求统计 + 诊断事件流），
不含任何判定逻辑——超阈染色是前端常量的职责，本层只算数。
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ReviewSessionBrief(BaseModel):
    """聚合范围内的单场基本信息（live_sessions 行投影 + 场次回顾行投影）。"""

    live_session_id: int
    title: Optional[str] = None
    source: str = "manual"
    platform: str = ""
    started_at_ms: int
    ended_at_ms: Optional[int] = None
    duration_ms: int = 0
    heat: int = 0
    viewer_count: int = 0
    audience_total: int = 0
    """本场累计观看人次（UV 口径；room.state 记账写入，无推送的场次为 0）。"""
    recap_text: Optional[str] = None
    """收播总结全文（session_recaps；未生成/功能前老场次为 None）。"""
    recap_created_at_ms: Optional[int] = None
    """收播总结的生成时刻（Unix 毫秒）。"""


class ReviewChips(BaseModel):
    """场次信息 chips（弹幕/进入/礼物/SC 计数，窗口内按 live_chat 三表计数）。"""

    danmaku_count: int = 0
    enter_count: int = 0
    gift_count: int = 0
    super_chat_count: int = 0
    gift_total_cny: Optional[float] = None
    """付费礼物折算人民币合计（银瓜子等免费币种不计入；无可折算金额为 None）。"""
    super_chat_total_cny: Optional[float] = None
    """SC 金额折算人民币合计（无可折算金额为 None）。"""


class ReviewSpeech(BaseModel):
    """场内发言与节奏（口径：live_chat 中 sender_role='assistant' 的行）。"""

    count: int = 0
    rate_per_min: Optional[float] = None
    longest_silence_ms: Optional[int] = None
    longest_silence_start_ms: Optional[int] = None
    """最长沉默区间的起点时刻（= 沉默前最后一条发言；零/单点发言为 None）。"""


class DecisionLatency(BaseModel):
    """决策定稿→开口的时延分位。

    口径：``planner.verdict``（表达生成之前发出）与 ``streamer.speech`` 按
    ``round_id`` 关联，取 speech 时刻减 verdict 时刻。不做事件流时序配对
    （speech 先于本轮 decision 收口发出，按先后猜配对会系统性错位）；也不取
    decision 自带的 ``total_duration_ms``（那是整轮总耗时，含思考与收尾，
    不是定稿→开口）。
    """

    sample_count: int = 0
    p50_ms: Optional[int] = None
    p90_ms: Optional[int] = None
    max_ms: Optional[int] = None
    max_at_ms: Optional[int] = None
    """最慢一轮的开口时刻（streamer.speech，供回看定位到时刻）。"""


class ReviewDecisions(BaseModel):
    """决策分布与时延（诊断事件流按事件名常量过滤后聚合）。"""

    total: int = 0
    distribution: Dict[str, int] = Field(default_factory=dict)
    """按 trigger_reason 计数的决策分布（含静默/失败轮）。"""
    silent_count: int = 0
    """被低置信度裁决压制的轮数（silent_reason 非空）。"""
    failed_count: int = 0
    """失败轮数（error 非空）。"""
    latency: DecisionLatency = Field(default_factory=DecisionLatency)


class LLMDomainStats(BaseModel):
    """单 profile（或总体）的 LLM 用量聚合（llm_requests 统计列）。"""

    profile_name: str = ""
    calls: int = 0
    failed_calls: int = 0
    slow_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    cache_hit_rate: Optional[float] = None
    cost: float = 0.0
    max_prompt_tokens: int = 0


class ReviewLLM(BaseModel):
    """LLM 用量/成本/健康（窗口切片聚合，复用缓存命中率口径）。"""

    overall: LLMDomainStats = Field(default_factory=LLMDomainStats)
    by_profile: List[LLMDomainStats] = Field(default_factory=list)


class WaterlinePoint(BaseModel):
    timestamp_ms: int
    prompt_tokens: int
    session_id: int


class WaterlineSeries(BaseModel):
    """单 profile 的 (timestamp, prompt_tokens) 时间序列（水位图数据源）。"""

    profile_name: str
    points: List[WaterlinePoint] = Field(default_factory=list)


class GameEventItem(BaseModel):
    """单条游戏事件（game_events 行投影，供复盘页明细列表）。"""

    game: str
    event_type: str
    message: str
    timestamp_ms: int


class ReviewGame(BaseModel):
    """场内游戏事件计数与明细（game_events 表按类型）。"""

    total: int = 0
    by_type: Dict[str, int] = Field(default_factory=dict)
    items: List[GameEventItem] = Field(default_factory=list)
    """明细列表（单场视图填充，按时间正序；合并视图为空）。"""
    truncated: bool = False
    """明细超出单场返回上限被截断（total > len(items) 时为真）。"""


class ViewerSpeakerItem(BaseModel):
    """活跃观众条目（单场发言计数）。"""

    viewer_id: int
    platform: str
    user_name: str = ""
    message_count: int = 0


class ReviewViewers(BaseModel):
    """场内观众侧聚合（单场视图；合并视图为 None）。"""

    active_total: int = 0
    """场内发过言的观众数（按内部观众 ID 去重）。"""
    new_viewers: int = 0
    """新观众数（历史首条发言落在本场窗口内）。"""
    returning_viewers: int = 0
    """回头观众数（场内发言但历史首条发言早于本场）。"""
    top_speakers: List[ViewerSpeakerItem] = Field(default_factory=list)
    """发言条数最多的观众（倒序，至多 5 位）。"""


class SessionReviewResponse(BaseModel):
    """场次复盘聚合响应（单场或多选合并共用一份形状）。"""

    sessions: List[ReviewSessionBrief] = Field(default_factory=list)
    window_start_ms: int = 0
    window_end_ms: int = 0
    chips: ReviewChips = Field(default_factory=ReviewChips)
    speech: ReviewSpeech = Field(default_factory=ReviewSpeech)
    decisions: ReviewDecisions = Field(default_factory=ReviewDecisions)
    llm: ReviewLLM = Field(default_factory=ReviewLLM)
    waterline: List[WaterlineSeries] = Field(default_factory=list)
    game: ReviewGame = Field(default_factory=ReviewGame)
    viewers: Optional[ReviewViewers] = None
    """观众侧聚合（单场视图填充；多选合并视图为 None）。"""
    report_file: Optional[str] = None
    """复盘可视化页文件名（logs/reviews 下 session-{id}- 前缀的 .html）。

    单场视图且报告文件存在时填充（经 /api/v1/review/files/ 服务）；
    多选合并视图或报告未生成时为 None。
    """
    degraded: bool = False
    """诊断事件天目录存在缺失（被清理或从未落盘）——决策侧指标相应降级。"""


class ReviewExportManifest(BaseModel):
    """数据包 manifest（打包口径与文件清单）。"""

    sessions: List[ReviewSessionBrief] = Field(default_factory=list)
    windows_ms: List[List[int]] = Field(default_factory=list)
    git_head: Optional[str] = None
    files: List[Dict[str, object]] = Field(default_factory=list)
    notice: str = "含观众数据，仅限开发组"


__all__ = [
    "DecisionLatency",
    "GameEventItem",
    "LLMDomainStats",
    "ReviewChips",
    "ReviewDecisions",
    "ReviewExportManifest",
    "ReviewGame",
    "ReviewLLM",
    "ReviewSessionBrief",
    "ReviewSpeech",
    "ReviewViewers",
    "SessionReviewResponse",
    "ViewerSpeakerItem",
    "WaterlinePoint",
    "WaterlineSeries",
]
