"""
v2 语义域事件 Payload 定义：tts.utterance.* 一次发声实例生命周期

定义 ``tts.utterance.started`` / ``tts.utterance.finished`` /
``tts.utterance.failed`` 三个事件的 Payload。

按契约：
- ``utterance_id`` 为全链路关联键（编排层生成），串联 reply 记录、TTS 事件、
  字幕、存储等通道，便于事后聚合与对账。
- 三个事件分别描述"开始出声 / 播放完成 / 失败"三个状态点；
  ``tts.utterance.*`` 是终点广播，消费者不得触发新决策（防环约束）。
- ``started`` 时机：流式引擎 = 首块 PCM 写声卡；全量引擎 = ``play_audio`` 调用。
  此时 ``duration_ms`` 对全量引擎为精确合成时长；对流式引擎合成未完时为 None。
- ``finished`` 时机：播放完成时刻（百毫秒级精度，不含硬件声卡缓冲残余）。
  ``duration_ms`` 由 PCM 样本数除以采样率精确计算得到。
- ``failed`` 时机：合成或播放过程中任何阶段失败。

设计要点：
- 三个事件分别对应不同的 Payload 类（started 含 ``speech_text`` 与 ``duration_ms``
  可选；finished 强调播放时长；failed 强调错误信息）——形状不同，分开定义比
  统一形状加判别字段更易读、更不易误填。
- ``@register_event`` 装饰器是幂等的；此处分别把三个类登记到对应事件名。
"""

from typing import Optional

from pydantic import Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms


@register_event("tts.utterance.started")
class UtteranceStartedPayload(BasePayload):
    """
    一次发声开始事件 Payload

    事件名：``tts.utterance.started`` —— TTS 引擎开始出声。

    发布者：TTS 工具自身（流式引擎在首块 PCM 写声卡时发布；全量引擎在
    ``play_audio`` 调用时发布）。
    订阅者：字幕写入器、emotion 同步、编排层排队等状态联动类消费者。

    Attributes:
        utterance_id: 一次发声实例的唯一 ID（编排层生成，格式 ``utt_{epoch_ms}_{seq}``，
            进程内自增、单场唯一），作为全链路关联键。
        speech_text: 本次发声对应的文本内容（与 reply.speech_text 一致）。
        engine: TTS 引擎标识（如 ``edge`` / ``gptsovits`` / ``omni`` / ``voicebox``）。
        duration_ms: 预计播放时长（Unix 毫秒）。全量引擎为合成后精确值；
            流式引擎合成未完时为 None（finished 事件才会给出准确值）。
        timestamp_ms: 事件发布时间戳（Unix 毫秒），用于日志/排序，与 started
            时刻解耦（防止事件总线异步分发时与实际发声时刻混用）。
    """

    utterance_id: str = Field(..., description="一次发声实例的唯一 ID（编排层生成，全链路关联键）")
    speech_text: str = Field(..., description="本次发声对应的文本内容")
    engine: str = Field(..., description="TTS 引擎标识（如 edge/gptsovits/omni/voicebox）")
    duration_ms: Optional[int] = Field(
        default=None,
        description="预计播放时长（Unix 毫秒）。全量引擎=合成后精确值；流式引擎合成未完=None",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


@register_event("tts.utterance.finished")
class UtteranceFinishedPayload(BasePayload):
    """
    一次发声播放完成事件 Payload

    事件名：``tts.utterance.finished`` —— TTS 引擎完成播放。

    发布者：TTS 工具自身在播放完成时刻发布（百毫秒级精度；声卡硬件缓冲残余
    不在信号内，因此 finished 事件不是播放端物理信号，而是工具回调信号）。
    订阅者：编排层（句末再决策 / 释放锁）、存储（落 reply 耗时）、后台记账器。

    Attributes:
        utterance_id: 一次发声实例的唯一 ID（与 started 事件一致，串联生命周期）。
        engine: TTS 引擎标识。
        duration_ms: 实际播放时长（Unix 毫秒）。由 PCM 样本数除以采样率精确计算，
            反映真实播放耗时而非合成耗时。
        timestamp_ms: 事件发布时间戳（Unix 毫秒），用于日志/排序。
    """

    utterance_id: str = Field(..., description="一次发声实例的唯一 ID（与 started 事件对应）")
    engine: str = Field(..., description="TTS 引擎标识")
    duration_ms: int = Field(..., description="实际播放时长（Unix 毫秒，PCM 样本数÷采样率精确计算）")
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


@register_event("tts.utterance.failed")
class UtteranceFailedPayload(BasePayload):
    """
    一次发声失败事件 Payload

    事件名：``tts.utterance.failed`` —— TTS 合成或播放失败。

    发布者：TTS 工具自身在合成或播放失败时发布（合成错误、WebSocket 断开、
    音频设备异常等任何阶段失败均触发）。
    订阅者：编排层（错误兜底 / 重试决策）、存储（落失败记录）、后台记账器。

    Attributes:
        utterance_id: 一次发声实例的唯一 ID（与 started 事件对应；若失败发生在
            started 之前则为编排层预生成）。
        engine: TTS 引擎标识。
        error_message: 失败原因描述（异常 message / 错误码 / 阶段标记），
            供编排层兜底决策与日志对账使用。
        timestamp_ms: 事件发布时间戳（Unix 毫秒），用于日志/排序。
    """

    utterance_id: str = Field(..., description="一次发声实例的唯一 ID（与 started 事件对应）")
    engine: str = Field(..., description="TTS 引擎标识")
    error_message: str = Field(..., description="失败原因描述（异常 message / 错误码 / 阶段标记）")
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


__all__ = [
    "UtteranceStartedPayload",
    "UtteranceFinishedPayload",
    "UtteranceFailedPayload",
]
