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

``ArchiveSnapshot`` —— 一场整理后她该记得的事（滚动档案快照）：每行引用一条档案行，
带"是否长期""最近一次写下或再提起是哪一场"两项快照自己的标注；原行一个字节都不动。

``SettleReport`` —— 一场整理的报告：失败原因，或成功时那些没能登记的条目
与原料裁剪说明。

``ViewerName`` —— ``viewer_names`` 一行：观众用过的昵称与首末出现时刻。

``Viewer`` —— ``viewers`` 一行加计算列 ``user_id``（代表外部 ID：账号 ID
优先，其次开放平台 ID，都没有为空串），供展示用。计数列为历史累计口径
（场次删除不回滚）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


__all__ = [
    "ArchiveLine",
    "ArchiveSnapshot",
    "Commitment",
    "CommitmentClosure",
    "CueLogEntry",
    "IndexCandidate",
    "IndexDoc",
    "IndexVector",
    "KeptSnapshotLine",
    "NewCueLogEntry",
    "PendingArchiveLine",
    "PendingCommitment",
    "SettleReport",
    "SnapshotChange",
    "SnapshotLine",
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
    save_id: str = Field(default="", description="答应时角色所在的游戏存档编号；空串 = 不分存档或存档不明")
    save_name: str = Field(default="", description="那个存档给人看的名字（只显示，不当键）")
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
    game: Optional[str] = Field(default=None, description="写的是哪个游戏世界里的事；None 即与游戏世界无关")
    save_id: str = Field(default="", description="那件事发生时角色所在的游戏存档编号；空串 = 不分存档或存档不明")
    save_name: str = Field(default="", description="那个存档给人看的名字（只显示，不当键）")
    human_locked: bool = Field(
        default=False, description="运营者改过或新增的行：之后的整理不能去掉或替换它，只有人能动"
    )
    keywords: List[str] = Field(
        default_factory=list, description="被动想起的触发词（名字、地名、物件名、梗的原词，至多 4 个）"
    )
    place: str = Field(default="", description="这件事挂在哪个记住的地点（地点名，只在 save_id 那个存档里成立）")
    source: str = Field(
        default="streamer", description="来源：streamer 她自己说的（整理写、人新增）/ viewer 人批准的观众候选"
    )
    viewer_chat_id: Optional[int] = Field(
        default=None, description="观众来源的行挂着的那条弹幕（live_chat 行）；她自己说的为空"
    )


class SnapshotLine(BaseModel):
    """快照里的一行：引用的档案行原样带出，加快照自己的三项标注。"""

    model_config = ConfigDict(extra="forbid")

    line: ArchiveLine = Field(..., description="引用的档案行（标题、原话、证据、场次、存档原样）")
    lasting: bool = Field(default=False, description="是否名字、住处、关系这类稳定的事（超出上限时先留）")
    seen_at_ms: int = Field(..., description="最近一次写下或再提起这件事的那一场的开播时刻")
    pinned: bool = Field(default=False, description="运营者放回快照或新增的行（取舍时排在稳定的事之前）")


class ArchiveSnapshot(BaseModel):
    """一场整理后她该记得的事：``owner_session_id`` 那一场新写的行加接住的前几场的行。

    ``stored`` 为 False 时是滚动之前的旧场次：没有存下的快照，由那一场与更早几场的档案行
    现拼（升级后的第一次滚动从这里接起）。
    """

    model_config = ConfigDict(extra="forbid")

    owner_session_id: int = Field(..., description="快照属于哪一场（它的整理写下了这份快照）")
    lines: List[SnapshotLine] = Field(default_factory=list, description="按快照位置排好的行")
    stored: bool = Field(default=True, description="是否存下的快照；False = 由旧档案行现拼")


class UnregisteredItem(BaseModel):
    """整理时看懂了却没能登记的条目：留原话给运营者在 WebUI 判断要不要手建。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["commitment", "archive"] = Field(..., description="本要登记成什么（承诺 / 档案行）")
    what: str = Field(default="", description="模型给的标题（可能为空）")
    quote: str = Field(default="", description="模型引的原话（校验没过时也原样留着）")
    reason: str = Field(..., description="没登记的原因（代码给的固定措辞）")


class SnapshotChange(BaseModel):
    """整理对上一份快照做的一处改动，写进整理报告给运营者核对。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["drop", "replace", "evicted", "refused"] = Field(
        ..., description="去掉 / 换成本场新写的一行 / 超出上限被挤出 / 模型要求了但没采纳"
    )
    what: str = Field(default="", description="被动到的那一行旧档案的标题")
    reason: str = Field(default="", description="理由：模型给的（去掉、替换）或代码给的固定措辞")
    quote: str = Field(default="", description="本场作证据的那句台词原文（去掉、替换时）")


class SettleReport(BaseModel):
    """一场的整理报告：失败原因，或成功时那些没能登记的条目与原料裁剪说明。"""

    model_config = ConfigDict(extra="forbid")

    failure_reason: Optional[str] = Field(default=None, description="整理失败的原因；成功时为 None")
    unregistered: List[UnregisteredItem] = Field(default_factory=list, description="没能登记、要人看一眼的条目")
    input_note: Optional[str] = Field(
        default=None, description="整理原料被裁剪的说明（台词超出模型窗口时）；未裁剪为 None"
    )
    snapshot_changes: List[SnapshotChange] = Field(
        default_factory=list, description="这一场对档案快照的改动：去掉、替换、超上限挤出、没采纳的操作"
    )


class PendingCommitment(BaseModel):
    """整理校验通过、等着一个事务写进去的新承诺。"""

    model_config = ConfigDict(extra="forbid")

    what: str = Field(..., description="动作标题（代码已截到上限）")
    quote: str = Field(default="", description="她播出原句的逐字片段")
    evidence_chat_id: Optional[int] = Field(default=None, description="那句台词的 live_chat 行")
    viewer_id: Optional[int] = Field(default=None, description="对某位观众；None 即不针对具体人")
    game: Optional[str] = Field(default=None, description="挂在哪个游戏；None 即不挂")
    save_id: str = Field(default="", description="答应时角色所在的游戏存档编号；空串 = 不分存档或存档不明")
    save_name: str = Field(default="", description="那个存档给人看的名字（只显示，不当键）")


class PendingArchiveLine(BaseModel):
    """整理校验通过、等着写进去的一行场次档案。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., description="标题（代码已截到上限）")
    quote: str = Field(..., description="她播出原句的逐字片段")
    evidence_chat_id: int = Field(..., description="原话所在的 live_chat 行")
    game: Optional[str] = Field(default=None, description="写的是哪个游戏世界里的事；None 即与游戏世界无关")
    save_id: str = Field(default="", description="那件事发生时角色所在的游戏存档编号；空串 = 不分存档或存档不明")
    save_name: str = Field(default="", description="那个存档给人看的名字（只显示，不当键）")
    lasting: bool = Field(default=False, description="是否名字、住处、关系这类稳定的事（快照标注）")
    in_snapshot: bool = Field(default=True, description="是否进本场快照；超出上限被挤出的照样写成本场档案行")
    keywords: List[str] = Field(default_factory=list, description="代码核过的触发词（至多 4 个）")
    place: str = Field(default="", description="代码核过的记住地点名；空串 = 不挂地点")


class KeptSnapshotLine(BaseModel):
    """整理决定接着记的一行旧快照：只引用原档案行，带更新后的快照标注。"""

    model_config = ConfigDict(extra="forbid")

    archive_line_id: int = Field(..., description="引用的档案行主键")
    lasting: bool = Field(default=False, description="是否稳定的事")
    seen_at_ms: int = Field(..., description="最近一次写下或再提起它的那一场的开播时刻")
    pinned: bool = Field(default=False, description="运营者放回或新增的行，接进新快照时照旧带着")


class CommitmentClosure(BaseModel):
    """一条旧承诺的了结依据：她说做完了或不做了的那句台词。"""

    model_config = ConfigDict(extra="forbid")

    commitment_id: int = Field(..., description="被了结的承诺主键")
    close_chat_id: Optional[int] = Field(default=None, description="了结依据的台词行")
    close_quote: Optional[str] = Field(default=None, description="了结那句的原话片段")


class IndexDoc(BaseModel):
    """一条待建检索索引的来源行（档案行、承诺、台词或游戏事件）：主键与建索引用的正文。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: int = Field(..., description="来源行主键（也是全文表的 rowid）")
    document: str = Field(..., description="建索引用的正文：档案行与承诺是标题加原话，台词与事件是正文本身")


class IndexCandidate(BaseModel):
    """检索的一条候选：来源行主键与这一路给的分数（全文是取正的 BM25，子串兜底为 0）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: int = Field(..., description="来源行主键")
    score: float = Field(default=0.0, description="这一路的分数，越大越相关")


class NewCueLogEntry(BaseModel):
    """一张推出去的线索卡（写进 ``memory_cue_log`` 的样子）：只有条目编号和分数，不带观众身份。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    live_session_id: int = Field(..., description="推在哪一场")
    pushed_at_ms: int = Field(..., description="推送时刻")
    card_kind: str = Field(
        ..., description="卡类：viewer 对象开口 / game 游戏开着 / place 地点 / rule 规则 / vector 向量 / joke 老梗"
    )
    archive_line_id: Optional[int] = Field(default=None, description="指向的档案行（档案卡才有）")
    commitment_id: Optional[int] = Field(default=None, description="指向的承诺（承诺卡才有）")
    sources: List[str] = Field(
        default_factory=list,
        description="命中它的全部来源：danmaku 观众弹幕 / place 游戏地点 / situation 游戏情境 / self 她的台词",
    )
    detail: Dict[str, Any] = Field(
        default_factory=dict, description="分数明细：规则写命中的触发词，向量写余弦、名次与融合分，地点写地点名与距离"
    )
    self_triggered: bool = Field(default=False, description="来源只有她自己的台词（防打转的统计口径）")


class CueLogEntry(NewCueLogEntry):
    """``memory_cue_log`` 一行：推出去的卡加主键与用上的时刻。"""

    id: int = Field(..., description="记录主键")
    used_at_ms: Optional[int] = Field(default=None, description="检测到她点名用上的时刻；没用上为空")


class IndexVector(BaseModel):
    """一条存下的向量：来源行主键与小端 float32 字节。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: int = Field(..., description="来源行主键")
    vector: bytes = Field(..., description="小端 float32 向量字节")
