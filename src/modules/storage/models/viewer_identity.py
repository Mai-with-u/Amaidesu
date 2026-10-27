"""观众身份处理域数据契约（待确认观众列表的读模型）

``PendingViewerSummary`` 是 ``ViewerIdentityRepo.list_pending`` 的行契约：
待确认观众行与其建议并入的老观众（候选）行的 LEFT JOIN 投影，候选观众
不存在时候选侧字段为 ``None``。消费方为 Dashboard 观众档案服务的待确认
页（人工比对合并建议）。

时间字段统一毫秒（Unix epoch 毫秒）。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


__all__ = ["PendingViewerSummary"]


class PendingViewerSummary(BaseModel):
    """待确认观众一行及其建议并入的候选观众摘要。"""

    model_config = ConfigDict(extra="forbid")

    viewer_id: int = Field(..., description="待确认观众的内部 ID")
    platform: str = Field(..., description="观众平台")
    user_name: str = Field(..., description="当前昵称")
    account_id: Optional[str] = Field(default=None, description="账号 ID（外部）；未记录为 None")
    open_id: Optional[str] = Field(default=None, description="开放平台 ID（外部）；未记录为 None")
    open_app_id: Optional[str] = Field(default=None, description="开放平台应用 ID")
    name_seen_ms: int = Field(default=0, description="当前昵称最近出现时刻（Unix epoch 毫秒）")
    message_count: int = Field(default=0, description="历史弹幕条数")
    interaction_count: int = Field(default=0, description="历史互动次数")
    paid_amount: int = Field(default=0, description="历史付费金额（平台最小虚拟货币单位）")
    last_active_ms: int = Field(default=0, description="最近活跃时刻（Unix epoch 毫秒）")
    candidate_id: Optional[int] = Field(default=None, description="建议并入的老观众 ID；无建议为 None")
    candidate_name: Optional[str] = Field(default=None, description="候选观众的当前昵称")
    candidate_account_id: Optional[str] = Field(default=None, description="候选观众的账号 ID")
    candidate_open_id: Optional[str] = Field(default=None, description="候选观众的开放平台 ID")
    candidate_name_seen_ms: Optional[int] = Field(default=None, description="候选观众当前昵称最近出现时刻")
    candidate_message_count: Optional[int] = Field(default=None, description="候选观众历史弹幕条数")
    candidate_interaction_count: Optional[int] = Field(default=None, description="候选观众历史互动次数")
    candidate_paid_amount: Optional[int] = Field(default=None, description="候选观众历史付费金额")
    candidate_last_active_ms: Optional[int] = Field(default=None, description="候选观众最近活跃时刻")
