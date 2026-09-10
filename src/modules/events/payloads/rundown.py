"""事件 Payload 定义：rundown.changed

流程单（Rundown）变更事件 Payload——``RundownState`` 唯一变更边界发出。

契约约定
--------
- ``RundownState`` 是该事件**唯一发布者**：每次 load / goto / next / pause / resume
  校验通过后即发射；拒绝路径（未知 id / 未达 min_duration）不发事件。
- ``segment_id`` 含义：
  - 正常切换：变更后的环节 id
  - finish（next 越过末段）：``segment_id=""`` 且 ``index == total``，订阅者据此识别"流程单走完"
- ``by`` 字段区分触发主体：
  - ``"agent"``：Agent 通过 ``RundownControlTool`` 触发
  - ``"human"``：Dashboard 控制台手动操作
  - ``"system"``：超时闹钟兜底硬切换（YAGNI，预留值；当前闹钟只提醒不执法）
- ``at_ms`` 为变更时刻（Unix 毫秒）；与 ``RundownState._resolve_now`` 解析出的时刻一致。
"""

from typing import Literal

from pydantic import ConfigDict, Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms


@register_event("rundown.changed")
class RundownChangedPayload(BasePayload):
    """流程单变更事件 Payload

    事件名：``rundown.changed``
    发布者：``RundownState``（唯一变更边界）
    订阅者：Dashboard（流程单控制台实时刷新）、事件记录器（事后回看"为什么跳到这里"）

    Attributes:
        rundown_id: 流程单 id（与 ``Rundown.rundown_id`` 一致）
        segment_id: 变更后的环节 id；finish 时为空字符串
        segment_title: 变更后的环节标题；finish 时为空字符串
        index: 变更后的环节下标（``0..total``；``total`` 表示 finish）
        total: 流程单总环节数
        by: 变更触发主体（``"agent"`` / ``"human"`` / ``"system"``）
        at_ms: 变更时刻（Unix 毫秒）
    """

    rundown_id: str = Field(..., description="流程单 id")
    segment_id: str = Field(default="", description="变更后的环节 id；finish 时为空字符串")
    segment_title: str = Field(default="", description="变更后的环节标题；finish 时为空字符串")
    index: int = Field(default=0, ge=0, description="变更后的环节下标（0..total；total 表示 finish）")
    total: int = Field(default=0, ge=0, description="流程单总环节数")
    by: Literal["agent", "human", "system"] = Field(
        default="agent",
        description="变更触发主体：agent=Agent 工具 / human=Dashboard 手动 / system=闹钟兜底",
    )
    at_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="变更时刻（Unix 毫秒）",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "rundown_id": "default_first_stream",
                "segment_id": "self_intro",
                "segment_title": "自我介绍",
                "index": 1,
                "total": 4,
                "by": "agent",
                "at_ms": 1706745600000,
            }
        }
    )


__all__ = ["RundownChangedPayload"]
