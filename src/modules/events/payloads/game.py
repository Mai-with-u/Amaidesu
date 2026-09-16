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

from typing import Any, ClassVar, Dict, List, Literal, Optional

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
        live_session_id: 场次主键（int）。发布方不填（保持默认 0），
          由场次盖章拦截器注入当前场次的存储主键；0 表示未归属
        game: 游戏标识（如 "minecraft"/"stardew_valley"）
        event_type: 事件类型 Literal（与 4 个事件名一一对应）
        message: 人类可读的描述（"挖到钻石了！" / "生命值低于 30%"）
        scene: 场景信息（关卡坐标 / 区块名等自由文本，留空表示无场景上下文）
        report_kind: 上报种类（仅 event_type="report" 时有值）
        timestamp_ms: 事件时间戳（Unix 毫秒）
    """

    # 判别字段：EventBus 在 emit 期校验"事件名末段 == 该字段值"，
    # 四重注册共享一类，挂错事件名直接报错
    _DISCRIMINANT_FIELD: ClassVar[str] = "event_type"

    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入",
    )
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
    # 任务上下文（超集形状：四类事件共用，缺省即"本次与身体事件无关"）。
    # 身体事件来自 MaiCraft 注意流（挨打/死亡/紧急反应），由游戏 Agent 观察后随上报携带；
    # 主播据此说"你在进行 xx 任务的时候遭遇了僵尸的攻击"，而不必自己去猜时间与结局。
    occurred_at_ms: int = Field(
        default=0,
        description="所述事实的发生时刻（Unix 毫秒；0 = 不适用或未知，不得编造）",
    )
    already_resolved: bool = Field(
        default=False,
        description="所述状况在上报时刻是否已经结束（供主播措辞滞后：已结束就不说'正在被攻击'）",
    )
    body_events: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="相关身体事件（本批任务期间观察到的注意流事实，最多若干条；空 = 无）",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件时间戳（Unix 毫秒）",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "live_session_id": 0,
                "game": "minecraft",
                "event_type": "milestone",
                "message": "挖到钻石了！",
                "scene": "y=-12, biome=deepslate",
                "timestamp_ms": 1706745600000,
            }
        }
    )


__all__ = ["GamePayload"]
