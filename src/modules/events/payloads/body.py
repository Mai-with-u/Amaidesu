"""
事件 Payload 定义：game.body.* 身体事件（AI 玩家遭遇，已叙事化）

上游是 MaiCraft 的注意流；**分类与叙事化**（哪些值得讲、怎么讲）由
``src/agents/minecraft/attention_matrix.py`` 负责——那是游戏侧知识。
本模块只定义事件契约：8 个具名事件共享一个 payload 类，``kind`` 为判别字段。

契约约定：
- **封闭集合**：``kind`` 8 个取值与 8 个事件名一一对应；上游类型可增可减，
  未知类型归 ``unknown`` 并保留 ``source_event_type``，事件面不随上游漂移。
- **遥测不入事件**：血量数值、坐标、游标编号不在这里；主播要这些时走
  工具直读（``perceive``），不占用叙事通道。
- ``summary`` 是一句可直接讲述的中文事实，由结构化字段生成，
  只陈述有证据的部分。
"""

from typing import Literal

from pydantic import ConfigDict, Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms

#: 叙事种类（判别字段取值；与 8 个事件名末段一一对应）
BodyKind = Literal[
    "attacked",
    "attack_ended",
    "died",
    "respawned",
    "reflex_started",
    "reflex_finished",
    "dimension_changed",
    "unknown",
]


@register_event("game.body.attacked")
@register_event("game.body.attack_ended")
@register_event("game.body.died")
@register_event("game.body.respawned")
@register_event("game.body.reflex_started")
@register_event("game.body.reflex_finished")
@register_event("game.body.dimension_changed")
@register_event("game.body.unknown")
class BodyEventPayload(BasePayload):
    """AI 玩家身体事件（已分类、已叙事化）。

    发布者：``maicraft_attention`` 采集器
    订阅者：主播 Agent（叙事素材）、Dashboard 观察面

    Attributes:
        live_session_id: 场次主键（int）。发布方不填（保持默认 0），
          由场次盖章拦截器注入当前场次的存储主键；0 表示未归属
        game: 游戏标识（当前只有 "minecraft"）
        kind: 叙事种类（判别字段；与事件名末段一致）
        summary: 一句可直接讲述的中文事实（只陈述有证据的部分）
        source_event_type: 上游注意流事件类型（如 ``agent.damaged``），留痕用
        attacker: 攻击者实体 id（如 ``minecraft:zombie``；无来源时为空串）
        hits: 该次遭遇的命中次数（来自上游片段聚合；0 = 上游未提供）
        resolved: 该次遭遇是否已结束（供主播措辞滞后：已结束就不说"正在被攻击"）
        occurred_at_ms: 上游事实发生时刻（Unix 毫秒；0 = 未提供或无法解析）
        timestamp_ms: 本系统发布该事件的时刻（Unix 毫秒）
    """

    # 判别字段：EventBus 在 emit 期校验"事件名末段 == 该字段值"，
    # 八重注册共享一类，挂错事件名直接报错
    _DISCRIMINANT_FIELD = "kind"

    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入",
    )
    game: str = Field(default="minecraft", description="游戏标识")
    kind: BodyKind = Field(..., description="叙事种类（与事件名末段一致）")
    summary: str = Field(default="", description="一句可直接讲述的中文事实")
    source_event_type: str = Field(default="", description="上游注意流事件类型（留痕）")
    attacker: str = Field(default="", description="攻击者实体 id；无来源时为空串")
    hits: int = Field(default=0, description="该次遭遇的命中次数（0 = 上游未提供）")
    resolved: bool = Field(default=False, description="该次遭遇是否已结束")
    occurred_at_ms: int = Field(default=0, description="上游事实发生时刻（Unix 毫秒；0 = 未知）")
    timestamp_ms: int = Field(default_factory=lambda: now_ms(), description="本系统发布时刻（Unix 毫秒）")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "live_session_id": 0,
                "game": "minecraft",
                "kind": "attacked",
                "summary": "正在被僵尸攻击（已命中 3 次）",
                "source_event_type": "agent.damaged",
                "attacker": "minecraft:zombie",
                "hits": 3,
                "resolved": False,
                "occurred_at_ms": 1789556183580,
                "timestamp_ms": 1789556184000,
            }
        }
    )


__all__ = ["BodyEventPayload", "BodyKind"]
