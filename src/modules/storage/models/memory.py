"""记忆域数据契约（session_recaps / viewer_profiles / viewers 表的读模型）

设计要点
--------

- 三个记忆域表读取结果的**持久化契约**：被主播 Agent 行为侧（场次回顾、
  常客画像）、storage 仓储（逐行反序列化）、Dashboard API（管理页投影）
  三方共同消费，依赖方向一律向下（agents → modules、dashboard → storage）。
- ``extra="forbid"``：与代码库其他 Pydantic 模型保持一致，严格拒绝未知字段。
- 时间字段统一毫秒（Unix epoch 毫秒），与仓库约定一致。

字段说明
--------

``SessionRecap`` —— ``session_recaps`` 一行：每场直播一份收播总结，
以 ``live_session_id`` 唯一定位。

``EndedSessionWithoutRecap`` —— 已结束却没有回顾的场次（启动补写的待办行），
来自 ``live_sessions`` LEFT JOIN ``session_recaps`` 的缺席侧，无回顾内容。

``ViewerProfile`` —— ``viewer_profiles`` JOIN ``viewers`` 的画像行：印象文本
（``viewer_profiles``）带上观众的平台与当前昵称（``viewers``，昵称随改名
漂移、权威在 viewers 表），消费方不必再逐个反查。

``ViewerName`` —— ``viewer_names`` 一行：观众用过的昵称与首末出现时刻。

``Viewer`` —— ``viewers`` 一行加计算列 ``user_id``（代表外部 ID：账号 ID
优先，其次开放平台 ID，都没有为空串），供展示用。计数列为历史累计口径
（场次删除不回滚）。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


__all__ = [
    "SessionRecap",
    "EndedSessionWithoutRecap",
    "ViewerProfile",
    "ViewerName",
    "Viewer",
]


class SessionRecap(BaseModel):
    """``session_recaps`` 一行：一场直播的收播总结。"""

    model_config = ConfigDict(extra="forbid")

    live_session_id: int = Field(..., description="场次主键（表内唯一）")
    started_at_ms: int = Field(..., description="该场开播时刻（Unix epoch 毫秒）")
    ended_at_ms: int = Field(..., description="该场收播时刻（Unix epoch 毫秒）")
    recap_text: str = Field(..., description="收播总结正文（LLM 生成）")
    created_at_ms: int = Field(..., description="总结写入时刻（Unix epoch 毫秒，覆盖写时刷新）")


class EndedSessionWithoutRecap(BaseModel):
    """已结束却没有回顾的场次（启动补写待办行；无回顾内容）。"""

    model_config = ConfigDict(extra="forbid")

    live_session_id: int = Field(..., description="场次主键")
    started_at_ms: int = Field(..., description="开播时刻（Unix epoch 毫秒）")
    ended_at_ms: int = Field(..., description="收播时刻（Unix epoch 毫秒，非空才进待办）")
    title: Optional[str] = Field(default=None, description="场次标题（可空）")


class ViewerProfile(BaseModel):
    """``viewer_profiles`` JOIN ``viewers`` 的画像行：印象 + 观众的平台与当前昵称。"""

    model_config = ConfigDict(extra="forbid")

    viewer_id: int = Field(..., description="内部观众 ID（viewers.id，画像表主键）")
    platform: str = Field(..., description="观众所在平台")
    user_name: str = Field(..., description="观众当前昵称（来自 viewers，随改名漂移）")
    profile_text: str = Field(..., description="印象文本（收播时 LLM 融合生成）")
    last_compressed_at_ms: int = Field(..., description="已整理到的时间点：该时刻前的弹幕与付费记录已融进印象")
    updated_at_ms: int = Field(..., description="印象最近更新时刻（Unix epoch 毫秒）")


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
    user_id: str = Field(..., description="代表外部 ID：账号 ID 优先，其次开放平台 ID，都没有为空串")
