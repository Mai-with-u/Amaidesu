"""
事件 Payload 定义：streamer.speech

定义 ``streamer.speech`` 主播发言业务事件 Payload。

``streamer`` 域代表主播 Agent 的业务事实层：本事件表达"主播已决定并生成
一条发言"这一业务事实，发布时刻早于 TTS 是否启用、是否真有声卡、字幕是否
启用——下游消费者（Simulator 节奏唤醒、存储记账落库、字幕器、
未来回放/字幕存档）拿到的是同一份业务信号。

字段约束：
- ``utterance_id`` 为全链路关联键（编排层生成，格式 ``utt_{epoch_ms}_{seq}``，
  进程内单调递增、单场唯一），与 ``tts.utterance.*`` 共用同一关联键，串联
  reply 记录、TTS 事件、字幕、存储等通道。
- ``live_session_id``：场次主键（live_sessions.id）。常规发言发布方不填（0），由
  事件总线的场次盖章拦截器按当前进行中场次补上；收播道别那一轮场次已结账、拦截器
  解析不到，由发布方填刚结束那一场的编号，拦截器只补 0、不覆盖非 0 值。补完仍为 0
  表示未归属（没开场次，或场次行已删/将删时的道别句），记账器据此不落库。
- ``reply_to_message_id``：本条发言回复的那条弹幕的 message_id（可选），来自
  Planner 决策输出——"主播回应了哪条观众消息"的关联事实，与 live_chat
  观众行的 message_id 构成外键关系，供互动分析查询；None 表示主动发言
  或未指向特定弹幕。
- ``emotion`` / ``emotion_intensity`` 必选：回复器对非法/缺失情绪降级
  ``neutral``、强度缺省 0.5 并 clamp 到 0–1，生产者保证必有值，故本事件
  的每条发言都携带完整情绪事实（17 枚举之一 + 强度）。
- ``target_user_id`` 可选：代表"这条发言回复的观众 user_id"，
  None 表示主动发言/无特定回复对象。下游存储记账器据此顺路维护
  viewers 的 ``replied_count`` 写穿。
"""

from typing import List, Optional

from pydantic import Field

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.utils.time_utils import now_ms


@register_event(CoreEvents.STREAMER_SPEECH)
class StreamerSpeechPayload(BasePayload):
    """
    主播发言业务事件 Payload

    事件名：``streamer.speech`` —— 主播 Agent 已生成一条发言。

    发布者：StreamerAgent（``_dispatch_speech_and_emotion``）。
    订阅者：SimulatorService（节奏唤醒）、StorageLedger（落 live_chat）、
    字幕器、未来回放/字幕存档等。

    Attributes:
        utterance_id: 一次发言实例的唯一 ID（编排层生成，格式
            ``utt_{epoch_ms}_{seq}``，进程内自增、单场唯一），全链路关联键，
            与 ``tts.utterance.*`` 共享同一键空间。
        live_session_id: 场次主键（live_sessions.id）。常规发言发布方不填（0），
            由场次盖章拦截器按进行中场次补；收播道别那一轮由发布方填刚结束那一场
            的编号，拦截器不覆盖非 0 值。补完仍为 0 表示未归属。
        reply_to_message_id: 本条发言回复的那条弹幕的 message_id（可选）；
            None 表示主动发言或未指向特定弹幕。
        reply_to_user_name / reply_to_text: 被回复弹幕的发送者昵称与原文
            （可选；与 reply_to_message_id 同源，决策期从本轮弹幕批解析，
            宁缺毋假）。字幕小部件随字幕显示引用行用；None 表示主动发言
            或未解析到。
        text: 主播发言文本（已 strip；空字符串不触发本事件）。
        emotion: 关联情绪标签（``Emotion`` 17 枚举值之一，小写；非法/缺失
            由回复器降级为 ``neutral``，生产者保证必有值）。
        emotion_intensity: 情绪强度（0.0–1.0；缺省 0.5，生产者已 clamp）。
        emotion_at_playback: 表情是否等这句开始播放（``tts.utterance.started``）再切。
            一次反应拆成几句、每句带自己的情绪时，后面几句发出时前一句还在播，
            立刻切表情会抢在声音前面；置 True 让形象按播放时刻对齐。
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
        description=(
            "场次主键（live_sessions.id）；常规发言发布方不填（0），由场次盖章拦截器按进行中场次补；"
            "收播道别那一轮由发布方填刚结束那一场的编号，拦截器不覆盖非 0 值；补完仍为 0=未归属"
        ),
    )
    reply_to_message_id: Optional[str] = Field(
        default=None,
        description="本条发言回复的那条弹幕的 message_id；None 表示主动发言或未指向特定弹幕",
    )
    reply_to_user_name: Optional[str] = Field(
        default=None,
        description=(
            "被回复弹幕的发送者昵称（与 reply_to_message_id 同源，决策期从本轮弹幕批解析）；"
            "None 表示主动发言或未解析到，展示位不渲染引用行"
        ),
    )
    reply_to_text: Optional[str] = Field(
        default=None,
        description=(
            "被回复弹幕的原文（与 reply_to_message_id 同源，决策期从本轮弹幕批解析）；"
            "None 表示主动发言或未解析到，展示位不渲染引用行"
        ),
    )
    text: str = Field(..., description="主播发言文本")
    emotion: str = Field(..., description="关联情绪标签（Emotion 17 枚举值之一，小写；必有值）")
    emotion_intensity: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="情绪强度（0.0–1.0；生产者保证必有值，缺省 0.5）",
    )
    emotion_at_playback: bool = Field(
        default=False,
        description="表情等这句开始播放再切（同一段里排在后面的句子）；False 时发言即切",
    )
    target_user_id: Optional[str] = Field(
        default=None,
        description="这条发言回复的观众 user_id（主动发言/无特定对象时为 None）",
    )
    llm_request_id: Optional[str] = Field(
        default=None,
        description="本次表达生成的 Replyer LLM 请求历史 ID（完整请求指针）；未进入生成阶段或上游未上报为 None",
    )
    memory_refs: Optional[List[str]] = Field(
        default=None,
        description="本条发言点名引用的记忆编号列表（如 ['约#7']）；未引用记忆时为 None",
    )
    reply_player_game: str = Field(
        default="",
        description=(
            "这条发言在回应的游戏玩家是哪个游戏的（本窗公屏聊天只来自一位核对过的玩家、且这句不是在回弹幕时才有）"
        ),
    )
    reply_player_uuid: str = Field(
        default="",
        description=(
            "这条发言在回应的游戏玩家的 UUID；本窗没人开口、两位以上开口、有认不出的人插话，或这句在回一条弹幕时为空"
        ),
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


__all__ = [
    "StreamerSpeechPayload",
]
