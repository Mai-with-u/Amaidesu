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


__all__ = [
    "DelegationRecord",
    "GameEventRecord",
    "GameEventTypeCount",
    "GamePlaceMark",
    "GameSaveSpan",
    "IndexedGameEvent",
]


class GameEventRecord(BaseModel):
    """``game_events`` 按场读取的一行：一条游戏事件记录。"""

    model_config = ConfigDict(extra="forbid")

    game: str = Field(..., description="产生事件的游戏标识")
    event_type: str = Field(..., description="事件类型（里程碑 / 安全阀 / 异常等）")
    message: str = Field(..., description="事件描述正文")
    scene: Optional[str] = Field(default=None, description="事件发生时的游戏场景；未记录为 None")
    timestamp_ms: int = Field(..., description="事件时刻（Unix epoch 毫秒）")
    save_id: str = Field(default="", description="事件发生时角色所在的游戏存档编号；空串 = 不分存档或存档不明")
    save_name: str = Field(default="", description="那个存档给人看的名字（只显示，不当键）")


class DelegationRecord(BaseModel):
    """一件委派从受理到了结的账（``game_events`` 里 ``delegation_*`` 行按任务号配对）。

    下播整理拿它核对"她说做好了"：只有 ``status=succeeded`` 是游戏侧正式交付过的；
    ``undelivered`` 是停了没交付，``interrupted`` 是重启打断、做到哪不确定。
    """

    model_config = ConfigDict(extra="forbid")

    live_session_id: int = Field(..., description="受理这件委派的场次（没有受理行时取了结那一行的场次）")
    task_id: str = Field(..., description="委派任务号")
    game: str = Field(..., description="执行的游戏 Agent 名")
    instruction: str = Field(default="", description="委派指令全文（含附带的来源对话）；没有受理行时为空串")
    status: str = Field(
        default="accepted",
        description=(
            "终态：accepted=还没了结；其余为任务账本终态（succeeded / undelivered / cancelled / failed / timeout）"
            "或 interrupted（重启打断）"
        ),
    )
    summary: str = Field(default="", description="终态摘要（交付内容、没交付时模型最后一句、撤单来源等）")
    started_ms: int = Field(default=0, description="受理时刻（Unix epoch 毫秒）；没有受理行时为 0")
    ended_ms: int = Field(default=0, description="了结时刻（Unix epoch 毫秒）；还没了结为 0")


class IndexedGameEvent(BaseModel):
    """按主键读回的一条游戏事件：检索命中后还原成行，回想工具据此渲染"哪一场、哪个存档"。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="game_events 主键")
    live_session_id: int = Field(..., description="事件所属场次")
    game: str = Field(..., description="产生事件的游戏标识")
    event_type: str = Field(..., description="事件类型（里程碑 / 阶段汇报等）")
    message: str = Field(..., description="事件描述正文")
    timestamp_ms: int = Field(..., description="事件时刻（Unix epoch 毫秒）")
    save_id: str = Field(default="", description="事件发生时角色所在的游戏存档编号；空串 = 不分存档或存档不明")
    save_name: str = Field(default="", description="那个存档给人看的名字（只显示，不当键）")


class GamePlaceMark(BaseModel):
    """一条地点动静（``game_events`` 里 ``place_*`` 类型的行）：哪个地点、什么动静、离多远、什么时候。

    下播整理据此认出一句话是在哪个地点说的、本场记住了哪些地点。
    """

    model_config = ConfigDict(extra="forbid")

    game: str = Field(..., description="游戏标识")
    kind: str = Field(..., description="动静种类：remembered / forgotten / near / left")
    place: str = Field(..., description="地点名")
    distance: Optional[int] = Field(default=None, description="走近时的水平距离（格）；其余为 None")
    timestamp_ms: int = Field(..., description="动静时刻（Unix epoch 毫秒）")
    save_id: str = Field(default="", description="当时所在的存档编号；空串 = 不分存档或存档不明")
    save_name: str = Field(default="", description="那个存档给人看的名字（只显示，不当键）")


class GameSaveSpan(BaseModel):
    """一场里某个游戏连续待在同一个存档的一段（由记下了存档的游戏事件按时间并成）。

    下播整理按台词时刻对上这些段，认出一句话是在哪个存档里说的。同一游戏 A → B → 回 A
    是三段；不分存档或存档不明的事件不参与。
    """

    model_config = ConfigDict(extra="forbid")

    game: str = Field(..., description="游戏标识")
    save_id: str = Field(..., description="存档编号（非空）")
    save_name: str = Field(default="", description="这一段里最后一次报的存档名（只显示）")
    started_ms: int = Field(..., description="这一段第一条事件的时刻（Unix epoch 毫秒）")
    ended_ms: int = Field(..., description="这一段最后一条事件的时刻（Unix epoch 毫秒）")


class GameEventTypeCount(BaseModel):
    """按事件类型计数的聚合行（复盘页游戏侧指标数据点）。"""

    model_config = ConfigDict(extra="forbid")

    event_type: str = Field(..., description="事件类型")
    n: int = Field(..., description="该类型的记录条数")
