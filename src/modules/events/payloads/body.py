"""
事件 Payload 定义：game.body.* 身体事件（动态族）

上游是 MaiCraft 的注意流：AI 玩家挨打、死亡、紧急反应这类**身体侧发生过的事实**。
采集器（``maicraft_attention``）常驻订阅该流，把重要事件转成这里的事件发出。

契约约定：
- **动态族**：事件名 = ``game.body.<上游事件类型>``（点号折叠为下划线，见
  :func:`body_event_name`）。上游事件类型由 Mod 定义、可增可减，无法逐一注册，
  因此走 ``register_event_family`` 动态族（与 ``tool.result.#`` 同性质），
  订阅方用 ``CoreEvents.GAME_BODY_WILDCARD`` 一站式监听。
- **三层名字的用意**：``game.*``（单层通配）是游戏 Agent 的低频里程碑通道，
  已被 ``StorageLedger`` 与主播订阅占用；身体事件是高频流，不与之混层。
- ``facts`` 原样携带上游 ``data``：事实由上游负责，本层只做搬运与语义化命名，
  不在这里加工数值（避免出现"看起来是本系统观测"的二手结论）。
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict

from pydantic import ConfigDict, Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event_family
from src.modules.time_utils import now_ms

#: 动态族前缀（register_event_family 要求以点号结尾）
GAME_BODY_FAMILY_PREFIX = "game.body."

#: 上游时间戳格式（Mod 侧 ISO-8601，带 Z 后缀；带/不带毫秒两种）
_UPSTREAM_TIME_FORMATS = ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ")


def body_event_name(event_type: str) -> str:
    """上游事件类型 → 事件名：``agent.damaged`` → ``game.body.agent_damaged``。

    事件名末段必须是**单个词元**（点号是层级分隔符），所以把点号等非
    ``[a-z0-9_]`` 字符折叠为下划线；空类型归到 ``unknown``，保证名字始终合法。
    """
    token = re.sub(r"[^a-z0-9_]+", "_", str(event_type or "").strip().lower()).strip("_")
    return f"{GAME_BODY_FAMILY_PREFIX}{token or 'unknown'}"


def upstream_timestamp_ms(raw: Any) -> int:
    """上游注意流时间戳（ISO-8601 带 Z）→ Unix 毫秒；无法解析返回 0。

    消费方（采集器转发、Agent 记录任务上下文）共用这一处解析：时间戳只做搬运，
    解析不出来宁可写 0（未知），也不拿"现在"冒充上游时刻。
    """
    if not isinstance(raw, str) or not raw:
        return 0
    text = raw.strip()
    for fmt in _UPSTREAM_TIME_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            continue
    return 0


class BodyEventPayload(BasePayload):
    """AI 玩家身体事件（注意流中继）。

    发布者：``maicraft_attention`` 采集器
    订阅者：主播 Agent（叙事素材）、Dashboard 观察面

    Attributes:
        live_session_id: 场次主键（int）。发布方不填（保持默认 0），
          由场次盖章拦截器注入当前场次的存储主键；0 表示未归属
        game: 游戏标识（当前只有 "minecraft"）
        event_type: 上游注意流事件类型（如 ``agent.damaged`` / ``agent.died``）
        priority: 上游优先级（``important`` / ``task`` / ``background``）
        message: 上游人类可读描述（英文原文，不做翻译以免产生二手结论）
        facts: 上游事件 ``data`` 原样携带（掉血、当前血量、攻击者、本能状态等）
        cursor: 该事件在注意流中的游标（同一上游流内单调递增）
        stream_id: 注意流编号（换世界/重启后变化，用于识别断代）
        occurred_at_ms: 上游事件发生时刻（由 ISO-8601 解析；0 = 上游未提供或无法解析）
        timestamp_ms: 本系统收到并发布该事件的时刻（Unix 毫秒）
    """

    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入",
    )
    game: str = Field(default="minecraft", description="游戏标识")
    event_type: str = Field(..., description="上游注意流事件类型（如 agent.damaged）")
    priority: str = Field(default="important", description="上游优先级")
    message: str = Field(default="", description="上游人类可读描述（原文）")
    facts: Dict[str, Any] = Field(default_factory=dict, description="上游事件 data 原样携带")
    cursor: int = Field(default=0, description="该事件在注意流中的游标")
    stream_id: str = Field(default="", description="注意流编号（断代识别）")
    occurred_at_ms: int = Field(default=0, description="上游事件发生时刻（Unix 毫秒；0 = 未提供或无法解析）")
    timestamp_ms: int = Field(default_factory=lambda: now_ms(), description="本系统发布时刻（Unix 毫秒）")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "live_session_id": 0,
                "game": "minecraft",
                "event_type": "agent.damaged",
                "priority": "important",
                "message": "The agent took damage",
                "facts": {
                    "current_health": 18.0,
                    "cause": {"causing_entity_type_id": "minecraft:zombie"},
                    "defense": {"policy": "instinct", "would_engage": True},
                },
                "cursor": 12,
                "stream_id": "0bbec5a8-01d5-45cd-8d79-aff8ef0c28ef",
                "occurred_at_ms": 1789556183580,
                "timestamp_ms": 1789556184000,
            }
        }
    )


# 动态族登记（装饰器先于类存在，此处回填真实 payload 类型）
register_event_family(GAME_BODY_FAMILY_PREFIX, BodyEventPayload)

__all__ = ["GAME_BODY_FAMILY_PREFIX", "BodyEventPayload", "body_event_name"]
