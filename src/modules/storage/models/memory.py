"""记忆域数据契约（承诺账本、场次档案与观众表的读模型）

设计要点
--------

- 两类表的**持久化契约**：记忆账本（commitments / archive_lines，由下播整理写入、
  WebUI 查看与纠错）与观众表（viewers / viewer_names）；被主播 Agent 行为侧、
  storage 仓储（逐行反序列化）、Dashboard API（管理页投影）三方共同消费，
  依赖方向一律向下（agents → modules、dashboard → storage）。
- ``extra="forbid"``：与代码库其他 Pydantic 模型保持一致，严格拒绝未知字段。
- 时间字段统一毫秒（Unix epoch 毫秒），与仓库约定一致。

字段说明
--------

``Commitment`` —— ``commitments`` 一行：她当众答应过、还没了结的事，
自带逐字原话作证据；原话所在场次被删时 evidence_chat_id 成为空引用。

``ArchiveLine`` —— ``archive_lines`` 一行：场次档案的标题与原话片段，
下次开播回想「上一场」时用。

``SettleReport`` —— 一场整理的报告：失败原因，或成功时那些没能登记的条目
与原料裁剪说明。

``ViewerName`` —— ``viewer_names`` 一行：观众用过的昵称与首末出现时刻。

``Viewer`` —— ``viewers`` 一行加计算列 ``user_id``（代表外部 ID：账号 ID
优先，其次开放平台 ID，都没有为空串），供展示用。计数列为历史累计口径
（场次删除不回滚）。
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


__all__ = [
    "ArchiveLine",
    "Commitment",
    "CommitmentClosure",
    "PendingArchiveLine",
    "PendingCommitment",
    "SettleReport",
    "UnregisteredItem",
    "ViewerName",
    "Viewer",
]


class ViewerName(BaseModel):
    """``viewer_names`` 一行：观众用过的昵称与首末出现时刻。"""

    model_config = ConfigDict(extra="forbid")

    user_name: str = Field(..., description="昵称")
    first_seen_ms: int = Field(..., description="首次出现时刻（Unix epoch 毫秒）")
    last_seen_ms: int = Field(..., description="最近出现时刻（Unix epoch 毫秒）")


class Viewer(BaseModel):
    """``viewers`` 一行加代表外部 ID 的计算列 ``user_id``。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="内部观众 ID（主键）")
    platform: str = Field(..., description="观众所在平台（同平台一位观众一行）")
    account_id: Optional[str] = Field(default=None, description="平台账号 ID（如 B 站 uid）；未采集为 None")
    open_id: Optional[str] = Field(default=None, description="开放平台发给某应用的 ID；未采集为 None")
    open_app_id: Optional[str] = Field(default=None, description="open_id 对应的应用 ID；存量迁移行可为空")
    user_name: str = Field(..., description="当前昵称")
    name_seen_ms: int = Field(..., description="当前昵称最近出现时刻（Unix epoch 毫秒）")
    simulated: bool = Field(default=False, description="是否模拟观众")
    link_status: str = Field(..., description="身份关联状态（confirmed / pending）")
    link_candidate_id: Optional[int] = Field(default=None, description="待并入的老观众 ID；仅 pending 态非空")
    message_count: int = Field(default=0, description="历史累计发言数")
    gift_count: int = Field(default=0, description="历史累计礼物次数")
    replied_count: int = Field(default=0, description="历史累计被主播回复数")
    interaction_count: int = Field(default=0, description="历史累计互动数（发言/礼物/付费/被回复）")
    paid_count: int = Field(default=0, description="历史累计付费次数（礼物 + SC + 上舰）")
    paid_amount: int = Field(default=0, description="历史累计付费金额（平台最小虚拟货币单位）")
    last_active_ms: int = Field(..., description="最近活跃时刻（Unix epoch 毫秒）")
    sessions_count: int = Field(default=0, description="来过几场正式场（历史累计）")
    last_session_id: Optional[int] = Field(default=None, description="上次计入来访场次的正式场主键")
    prev_seen_ms: Optional[int] = Field(default=None, description="本场之前最近一次活跃时刻（Unix epoch 毫秒）")
    forget_requested_at_ms: Optional[int] = Field(
        default=None, description="宽限删除请求时刻（Unix epoch 毫秒）；None 即无待执行删除"
    )
    user_id: str = Field(..., description="代表外部 ID：账号 ID 优先，其次开放平台 ID，都没有为空串")


class Commitment(BaseModel):
    """``commitments`` 一行：她当众答应过、还没了结的事。

    ``what`` 是动作标题（≤30 字，不写缘由），``quote`` 是她播出原句的逐字片段；
    原句所在场次被删时 ``evidence_chat_id`` 成为空引用，读方按"原句所在场次已删除"展示。
    """

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="承诺主键（自增）")
    what: str = Field(..., description="动作标题（≤30 字，不写缘由）")
    quote: str = Field(default="", description="她播出原句的逐字片段（≤80 字）；运营者手建为空")
    evidence_chat_id: Optional[int] = Field(default=None, description="原话所在的 live_chat 行；手建为空")
    viewer_id: Optional[int] = Field(default=None, description="对某位观众（内部观众 ID）；None 即不针对具体人")
    game: Optional[str] = Field(default=None, description="挂在哪个游戏；None 即不挂游戏")
    status: Literal["open", "closed", "void"] = Field(
        default="open", description="open 未了结 / closed 已了结（做完或放弃）/ void 运营者判误记"
    )
    created_session_id: Optional[int] = Field(default=None, description="登记它的场次；运营者手建为空")
    close_chat_id: Optional[int] = Field(default=None, description="了结依据：她说做完了或不做了的那句台词行")
    close_quote: Optional[str] = Field(default=None, description="了结那句的原话片段")
    created_at_ms: int = Field(..., description="登记时刻（Unix epoch 毫秒）")
    updated_at_ms: int = Field(..., description="最近更新时刻（Unix epoch 毫秒）")


class ArchiveLine(BaseModel):
    """``archive_lines`` 一行：场次档案里的一条，下次开播回想"上一场"时用。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="档案行主键（自增）")
    live_session_id: int = Field(..., description="所属场次主键")
    text: str = Field(..., description="标题（≤30 字；游戏相关的写上游戏名）")
    quote: str = Field(..., description="她播出原句的逐字片段（≤80 字）")
    evidence_chat_id: int = Field(..., description="原话所在的 live_chat 行")


class UnregisteredItem(BaseModel):
    """整理时看懂了却没能登记的条目：留原话给运营者在 WebUI 判断要不要手建。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["commitment", "archive"] = Field(..., description="本要登记成什么（承诺 / 档案行）")
    what: str = Field(default="", description="模型给的标题（可能为空）")
    quote: str = Field(default="", description="模型引的原话（校验没过时也原样留着）")
    reason: str = Field(..., description="没登记的原因（代码给的固定措辞）")


class SettleReport(BaseModel):
    """一场的整理报告：失败原因，或成功时那些没能登记的条目与原料裁剪说明。"""

    model_config = ConfigDict(extra="forbid")

    failure_reason: Optional[str] = Field(default=None, description="整理失败的原因；成功时为 None")
    unregistered: List[UnregisteredItem] = Field(default_factory=list, description="没能登记、要人看一眼的条目")
    input_note: Optional[str] = Field(
        default=None, description="整理原料被裁剪的说明（台词超出模型窗口时）；未裁剪为 None"
    )


class PendingCommitment(BaseModel):
    """整理校验通过、等着一个事务写进去的新承诺。"""

    model_config = ConfigDict(extra="forbid")

    what: str = Field(..., description="动作标题（代码已截到上限）")
    quote: str = Field(default="", description="她播出原句的逐字片段")
    evidence_chat_id: Optional[int] = Field(default=None, description="那句台词的 live_chat 行")
    viewer_id: Optional[int] = Field(default=None, description="对某位观众；None 即不针对具体人")
    game: Optional[str] = Field(default=None, description="挂在哪个游戏；None 即不挂")


class PendingArchiveLine(BaseModel):
    """整理校验通过、等着写进去的一行场次档案。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., description="标题（代码已截到上限）")
    quote: str = Field(..., description="她播出原句的逐字片段")
    evidence_chat_id: int = Field(..., description="原话所在的 live_chat 行")


class CommitmentClosure(BaseModel):
    """一条旧承诺的了结依据：她说做完了或不做了的那句台词。"""

    model_config = ConfigDict(extra="forbid")

    commitment_id: int = Field(..., description="被了结的承诺主键")
    close_chat_id: Optional[int] = Field(default=None, description="了结依据的台词行")
    close_quote: Optional[str] = Field(default=None, description="了结那句的原话片段")
