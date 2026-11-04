"""记忆账本接口的数据契约：承诺、场次档案、整理状态。"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from src.modules.storage.models.memory import SettleReport


class CommitmentItem(BaseModel):
    """承诺账本的一行：动作标题、她说的原话，以及对谁、挂哪个游戏。"""

    id: int = Field(description="承诺主键")
    what: str = Field(description="动作标题")
    quote: str = Field(description="她播出原句的逐字片段；运营者手建为空")
    viewer_id: Optional[int] = Field(default=None, description="对某位观众（内部观众 ID）；空 = 不针对具体人")
    viewer_name: Optional[str] = Field(default=None, description="那位观众当前的昵称（读不到时为空）")
    game: Optional[str] = Field(default=None, description="挂在哪个游戏；空 = 不挂")
    save_id: str = Field(default="", description="答应时角色所在的游戏存档编号；空 = 不分存档或存档不明")
    save_name: str = Field(default="", description="那个存档给人看的名字（只显示）")
    status: str = Field(description="open 未了结 / closed 已了结 / void 判误记")
    created_session_id: Optional[int] = Field(default=None, description="登记它的场次")
    close_quote: Optional[str] = Field(default=None, description="了结那句的原话")
    created_at_ms: int = Field(description="登记时刻（Unix 毫秒）")
    updated_at_ms: int = Field(description="最近更新时刻（Unix 毫秒）")


class CommitmentListResponse(BaseModel):
    """承诺列表：筛选后的行与两个计数（页面顶部与筛选按钮用）。"""

    items: List[CommitmentItem] = Field(default_factory=list, description="承诺行，新的在前")
    total: int = Field(description="筛选条件下的条数")
    open_count: int = Field(description="未了结条数")
    void_count: int = Field(description="判误记条数")


class CommitmentCreateRequest(BaseModel):
    """运营者手建一条承诺（例如把旧笔记里还想兑现的事搬过来）。"""

    what: str = Field(..., min_length=1, max_length=60, description="动作标题")
    quote: str = Field(default="", max_length=200, description="她当时说的原话（可空）")
    viewer_id: Optional[int] = Field(default=None, description="对某位观众（内部观众 ID）；空 = 不针对具体人")
    game: Optional[str] = Field(default=None, max_length=60, description="挂在哪个游戏")


class CommitmentCloseRequest(BaseModel):
    """运营者把一条承诺标成已了结。"""

    close_quote: str = Field(default="", max_length=200, description="了结依据的原话（可空）")


class ArchiveLineItem(BaseModel):
    """场次档案的一行：标题加她说的原话，游戏里的事带上游戏与存档。"""

    id: int = Field(description="档案行主键")
    live_session_id: int = Field(description="所属场次")
    text: str = Field(description="标题")
    quote: str = Field(description="她播出原句的逐字片段")
    evidence_chat_id: int = Field(description="原话所在的台词行")
    game: Optional[str] = Field(default=None, description="写的是哪个游戏里的事；空 = 与游戏无关")
    save_id: str = Field(default="", description="那件事发生时角色所在的游戏存档编号；空 = 不分存档或存档不明")
    save_name: str = Field(default="", description="那个存档给人看的名字（只显示）")


class ArchiveListResponse(BaseModel):
    """某一场的档案行（按写入顺序）。"""

    live_session_id: int = Field(description="场次主键")
    items: List[ArchiveLineItem] = Field(default_factory=list, description="档案行")


class SettleSessionItem(BaseModel):
    """一场正式场的整理状态。"""

    live_session_id: int = Field(description="场次主键")
    title: Optional[str] = Field(default=None, description="场次标题")
    started_at_ms: int = Field(description="开播时刻（Unix 毫秒）")
    ended_at_ms: Optional[int] = Field(default=None, description="收播时刻（Unix 毫秒）")
    settled_at_ms: Optional[int] = Field(default=None, description="整理完成时刻；空 = 待整理")
    settle_attempts: int = Field(description="自动重试次数")
    settle_report: Optional[SettleReport] = Field(default=None, description="整理报告：失败原因或未登记清单")
    archive_count: int = Field(description="本场写了几行档案")


class SettleListResponse(BaseModel):
    """整理状态列表：待整理场数与最近的正式场整理状态。"""

    pending_count: int = Field(description="待整理（且自动重试没用完）的正式场数")
    max_attempts: int = Field(description="自动重试上限")
    items: List[SettleSessionItem] = Field(default_factory=list, description="最近的正式场，新的在前")


class LedgerMutationResponse(BaseModel):
    """账本上的写操作统一回执。"""

    success: bool = Field(description="是否改到了数据")
    detail: str = Field(default="", description="给页面看的说明")
