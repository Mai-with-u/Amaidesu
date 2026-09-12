"""
事件 Payload 定义：perception.* 主播感知流

定义主播 Agent 对直播内容的感知事件（视觉等）。感知流不是观众行为、
不是房间消息——不落 live_chat（避免"屏幕描述当弹幕"的数据污染），
消费方为主播 Agent 的决策上下文（环境参考）。
"""

from pydantic import ConfigDict, Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms


@register_event("perception.screen")
class ScreenDescriptionPayload(BasePayload):
    """
    屏幕画面描述事件（主播视觉感知）

    发布者：screen 采集器（差异检测 + VLM 分析）
    订阅者：主播 Agent（画面描述进决策上下文的环境参考）

    Attributes:
        content: 画面描述文本（VLM 生成的当前画面摘要）
        timestamp_ms: 事件时间戳（Unix 毫秒）
    """

    content: str = Field(..., description="画面描述文本（VLM 生成的当前画面摘要）")
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件时间戳（Unix 毫秒）",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content": "主播正在玩《双人成行》，画面中是合作解谜关卡",
                "timestamp_ms": 1706745600000,
            }
        }
    )


__all__ = ["ScreenDescriptionPayload"]
