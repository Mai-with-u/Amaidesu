"""
v2 语义域事件 Payload 定义：streamer.speech

定义 ``streamer.speech`` 主播发言业务事件 Payload。

``streamer`` 域代表主播 Agent 的业务事实层：本事件表达"主播已决定并生成
一条发言"这一业务事实，发布时刻早于 TTS 是否启用、是否真有声卡、字幕是否
启用——下游消费者（Simulator 节奏唤醒、ContextService 历史写入、字幕器、
未来回放/字幕存档）拿到的是同一份业务信号。

字段约束：
- ``utterance_id`` 为全链路关联键（编排层生成，格式 ``utt_{epoch_ms}_{seq}``，
  进程内单调递增、单场唯一），与 ``tts.utterance.*`` 共用同一关联键，串联
  reply 记录、TTS 事件、字幕、存储等通道。
- 不携带 ``live_session_id``：场次归属由订阅方按自身上下文关联（先例：
  ``tts.utterance.*`` Payload 同样不带，场次归属归订阅方解析）。
- ``emotion`` 可选：存在则带上，不存在显式 None，便于下游按字段过滤。
"""

from typing import Optional

from pydantic import Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms


@register_event("streamer.speech")
class StreamerSpeechPayload(BasePayload):
    """
    主播发言业务事件 Payload

    事件名：``streamer.speech`` —— 主播 Agent 已生成一条发言。

    发布者：StreamerAgent（``_dispatch_speech_and_emotion``）。
    订阅者：SimulatorService（节奏唤醒）、ContextService 写入历史、字幕
    器、未来回放/字幕存档等。

    Attributes:
        utterance_id: 一次发言实例的唯一 ID（编排层生成，格式
            ``utt_{epoch_ms}_{seq}``，进程内自增、单场唯一），全链路关联键，
            与 ``tts.utterance.*`` 共享同一键空间。
        text: 主播发言文本（已 strip；空字符串不触发本事件）。
        emotion: 关联情绪标签（可选；有则带上）。
        timestamp_ms: 事件发布时间戳（Unix 毫秒），用于日志/排序，
            与回复生成时刻解耦（防止事件总线异步分发时与实际发言时刻混用）。
    """

    utterance_id: str = Field(..., description="一次发言实例的唯一 ID（编排层生成，全链路关联键）")
    text: str = Field(..., description="主播发言文本")
    emotion: Optional[str] = Field(default=None, description="关联情绪标签（可选）")
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


__all__ = [
    "StreamerSpeechPayload",
]
