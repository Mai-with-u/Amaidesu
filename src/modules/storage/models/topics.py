"""话题域数据契约（timeline_summary 表的读模型）

``timeline_summary`` 一行 = 一段摘要历史（BackgroundMaintainer 摘要成功后
写入）。``TimelineSummaryEntry`` 是该表按场读取的持久化契约，消费方为
主播 Agent 的场次回顾（话题轨迹原料）。

时间字段统一毫秒（Unix epoch 毫秒）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


__all__ = ["TimelineSummaryEntry"]


class TimelineSummaryEntry(BaseModel):
    """``timeline_summary`` 按场读取的一行：一段摘要及其覆盖时间窗。"""

    model_config = ConfigDict(extra="forbid")

    start_ms: int = Field(..., description="摘要覆盖窗起点（Unix epoch 毫秒）")
    end_ms: int = Field(..., description="摘要覆盖窗终点（Unix epoch 毫秒）")
    summary: str = Field(..., description="该时间窗的摘要正文")
