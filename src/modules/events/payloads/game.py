"""
v2 语义域事件 Payload 定义：game.* 游戏里程碑

定义 3 类游戏事件 Payload（milestone / attention_required / error）。
对应存储 ``game_events`` 表（见 .omo/drafts/amaidesu-v2-storage-schema.md）。

按契约（.omo/drafts/amaidesu-v2-event-contract.md "game.*" 节）：
- 低频、只发重大变化（挖到钻石 / 通关章节 / 安全阀偏差 / 异常）
- 同一 ``GamePayload`` 类在 3 个事件名下复用，通过 ``event_type`` 字段判别
- ``scene`` 字段携带场景信息（关卡坐标 / 区块名等自由文本），便于回顾
"""

from typing import Literal

from pydantic import ConfigDict, Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms


@register_event("game.milestone")
@register_event("game.attention_required")
@register_event("game.error")
class GamePayload(BasePayload):
    """
    游戏里程碑事件 Payload（统一形状 + event_type 判别）

    事件名：
    - ``game.milestone`` — 重大进展（挖到钻石 / 通关章节）
    - ``game.attention_required`` — 安全阀偏差报告（"我先回血再去挖钻石"）
    - ``game.error`` — 游戏异常

    发布者：游戏 Agent（§1.49 BaseAgent 事件上报面）
    订阅者：主播 Planner（监听重大变化做决策）

    Attributes:
        live_session_id: 场次 ID（与存储 game_events FK 一致）
        game: 游戏标识（如 "minecraft"/"stardew_valley"）
        event_type: 事件类型 Literal（与 3 个事件名一一对应）
        message: 人类可读的描述（"挖到钻石了！" / "生命值低于 30%"）
        scene: 场景信息（关卡坐标 / 区块名等自由文本，留空表示无场景上下文）
        timestamp_ms: 事件时间戳（Unix 毫秒）
    """

    live_session_id: str = Field(..., description="场次唯一 ID")
    game: str = Field(..., description="游戏标识（如 'minecraft'/'stardew_valley'）")
    event_type: Literal["milestone", "attention_required", "error"] = Field(
        ...,
        description="事件类型。3 类与 game.* 三事件名一一对应，通配订阅 game.* 时按此分发",
    )
    message: str = Field(..., description="人类可读的描述")
    scene: str = Field(default="", description="场景信息（关卡坐标/区块名等自由文本）")
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件时间戳（Unix 毫秒）",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "live_session_id": "ls_20260822_001",
                "game": "minecraft",
                "event_type": "milestone",
                "message": "挖到钻石了！",
                "scene": "y=-12, biome=deepslate",
                "timestamp_ms": 1706745600000,
            }
        }
    )


__all__ = ["GamePayload"]
