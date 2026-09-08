"""
事件 Payload 定义：streamer.speech

定义 ``streamer.speech`` 主播发言业务事件 Payload。

``streamer`` 域代表主播 Agent 的业务事实层：本事件表达"主播已决定并生成
一条发言"这一业务事实，发布时刻早于 TTS 是否启用、是否真有声卡、字幕是否
启用——下游消费者（Simulator 节奏唤醒、ContextService 历史写入、字幕器、
未来回放/字幕存档）拿到的是同一份业务信号。

字段约束：
- ``utterance_id`` 为全链路关联键（编排层生成，格式 ``utt_{epoch_ms}_{seq}``，
  进程内单调递增、单场唯一），与 ``tts.utterance.*`` 共用同一关联键，串联
  reply 记录、TTS 事件、字幕、存储等通道。
- ``live_session_id``：场次主键（live_sessions.id）。发布方不填，由事件总线的
  场次盖章拦截器统一注入当前进行中场次；0 表示未归属。
- ``reply_to_message_id``：本条发言回复的那条弹幕的 message_id（可选），来自
  Planner 决策输出——"主播回应了哪条观众消息"的关联事实，与 live_chat
  观众行的 message_id 构成外键关系，供互动分析查询；None 表示主动发言
  或未指向特定弹幕。
- ``emotion`` 可选：存在则带上，不存在显式 None，便于下游按字段过滤。
- ``target_user_id`` 可选：代表"这条发言回复的观众 user_id"，
  None 表示主动发言/无特定回复对象。下游存储记账器据此顺路维护
  viewers 的 ``replied_count`` 写穿。
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
        live_session_id: 场次主键（live_sessions.id）；发布方不填，由场次盖章
            拦截器注入当前进行中场次；0 表示未归属。
        reply_to_message_id: 本条发言回复的那条弹幕的 message_id（可选）；
            None 表示主动发言或未指向特定弹幕。
        text: 主播发言文本（已 strip；空字符串不触发本事件）。
        emotion: 关联情绪标签（可选；有则带上）。
        target_user_id: 这条发言回复的观众 user_id（可选；主动发言/无特定
            对象时为 ``None``）。下游落库组件可据此维护 viewers 的
            ``replied_count`` 写穿。
        timestamp_ms: 事件发布时间戳（Unix 毫秒），用于日志/排序，
            与回复生成时刻解耦（防止事件总线异步分发时与实际发言时刻混用）。
    """

    utterance_id: str = Field(..., description="一次发言实例的唯一 ID（编排层生成，全链路关联键）")
    round_id: Optional[str] = Field(
        default=None,
        description="关联决策轮次 ID；发言发生在主播决策轮上下文内时填写（观察器成组用）",
    )
    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入；0=未归属",
    )
    reply_to_message_id: Optional[str] = Field(
        default=None,
        description="本条发言回复的那条弹幕的 message_id；None 表示主动发言或未指向特定弹幕",
    )
    text: str = Field(..., description="主播发言文本")
    emotion: Optional[str] = Field(default=None, description="关联情绪标签（可选）")
    target_user_id: Optional[str] = Field(
        default=None,
        description="这条发言回复的观众 user_id（主动发言/无特定对象时为 None）",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


__all__ = [
    "StreamerSpeechPayload",
]
