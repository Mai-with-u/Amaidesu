"""
事件 Payload 定义：game.* 游戏事件

定义 4 类游戏事件 Payload（milestone / attention_required / error / report）。
对应存储 ``game_events`` 表。

契约约定：
- 低频、只发重大变化（挖到钻石 / 通关章节 / 安全阀偏差 / 异常 / 交付与升级）
- 同一 ``GamePayload`` 类在 4 个事件名下复用，通过 ``event_type`` 字段判别
- ``scene`` 字段携带场景信息（关卡坐标 / 区块名等自由文本），便于回顾
- ``report_kind`` 仅 report 事件使用：delivery=交付总结 / escalation=升级决策
"""

from typing import Literal, Optional

from pydantic import ConfigDict, Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms


@register_event("game.milestone")
@register_event("game.attention_required")
@register_event("game.error")
@register_event("game.report")
class GamePayload(BasePayload):
    """
    游戏事件 Payload（统一形状 + event_type 判别）

    事件名：
    - ``game.milestone`` — 重大进展（挖到钻石 / 通关章节）
    - ``game.attention_required`` — 安全阀偏差报告（"我先回血再去挖钻石"）
    - ``game.error`` — 游戏异常
    - ``game.report`` — 游戏 Agent 主动向派发方上报（交付总结 / 升级决策）

    发布者：游戏 Agent（BaseAgent 事件上报面）
    订阅者：主播 Planner（监听重大变化做决策）

    Attributes:
        live_session_id: 场次 ID（与存储 game_events FK 一致）
        game: 游戏标识（如 "minecraft"/"stardew_valley"）
        event_type: 事件类型 Literal（与 4 个事件名一一对应）
        message: 人类可读的描述（"挖到钻石了！" / "生命值低于 30%"）
        scene: 场景信息（关卡坐标 / 区块名等自由文本，留空表示无场景上下文）
        report_kind: 上报种类（仅 event_type="report" 时有值）
        timestamp_ms: 事件时间戳（Unix 毫秒）
    """

    live_session_id: str = Field(..., description="场次唯一 ID")
    game: str = Field(..., description="游戏标识（如 'minecraft'/'stardew_valley'）")
    event_type: Literal["milestone", "attention_required", "error", "report"] = Field(
        ...,
        description="事件类型。4 类与 game.* 四事件名一一对应，通配订阅 game.* 时按此分发",
    )
    message: str = Field(..., description="人类可读的描述")
    scene: str = Field(default="", description="场景信息（关卡坐标/区块名等自由文本）")
    report_kind: Optional[Literal["delivery", "escalation"]] = Field(
        default=None,
        description="上报种类（仅 event_type='report' 时有值：delivery=交付总结 / escalation=升级决策）",
    )
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
