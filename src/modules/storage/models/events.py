"""游戏事件域数据契约（game_events 表的读模型）

``game_events`` 一行 = 一条游戏里程碑/安全阀/异常记录（StorageLedger 从
``game.*`` 事件落库）。本文件是该表读取结果的持久化契约，被主播 Agent
（场次回顾的游戏进展原料）与 Dashboard 复盘聚合（按类型计数与明细列表）
共同消费。

``GameEventTypeCount`` 是聚合行（GROUP BY 派生，非表行）。

时间字段统一毫秒（Unix epoch 毫秒）。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


__all__ = ["GameEventRecord", "GameEventTypeCount"]


class GameEventRecord(BaseModel):
    """``game_events`` 按场读取的一行：一条游戏事件记录。"""

    model_config = ConfigDict(extra="forbid")

    game: str = Field(..., description="产生事件的游戏标识")
    event_type: str = Field(..., description="事件类型（里程碑 / 安全阀 / 异常等）")
    message: str = Field(..., description="事件描述正文")
    scene: Optional[str] = Field(default=None, description="事件发生时的游戏场景；未记录为 None")
    timestamp_ms: int = Field(..., description="事件时刻（Unix epoch 毫秒）")


class GameEventTypeCount(BaseModel):
    """按事件类型计数的聚合行（复盘页游戏侧指标数据点）。"""

    model_config = ConfigDict(extra="forbid")

    event_type: str = Field(..., description="事件类型")
    n: int = Field(..., description="该类型的记录条数")
