"""
事件 Payload 定义：tts.utterance.* 一次发声实例生命周期

定义 ``tts.utterance.started`` / ``tts.utterance.finished`` /
``tts.utterance.failed`` / ``tts.utterance.dropped`` /
``tts.utterance.sentence_boundary`` 五个事件的 Payload。

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
  可选；finished 强调播放时长；failed 强调错误信息并必带 ``speech_text`` 原文，
  供无声音时照常显示文本）——形状不同，分开定义比统一形状加判别字段更易读、更不易误填。
- ``@register_event`` 装饰器是幂等的；此处分别把三个类登记到对应事件名。
"""

from typing import Optional

from pydantic import Field

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.utils.time_utils import now_ms


@register_event(CoreEvents.TTS_UTTERANCE_STARTED)
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
        live_session_id: 场次主键（发布方不填，由场次盖章拦截器注入；0=未归属）。
            TTS 发声发生在场次进行中，盖章后回看可按场次过滤命中。
    """

    utterance_id: str = Field(..., description="一次发声实例的唯一 ID（编排层生成，全链路关联键）")
    speech_text: str = Field(..., description="本次发声对应的文本内容")
    engine: str = Field(..., description="TTS 引擎标识（如 edge/gptsovits/omni/voicebox）")
    duration_ms: Optional[int] = Field(
        default=None,
        description="预计播放时长（Unix 毫秒）。全量引擎=合成后精确值；流式引擎合成未完=None",
    )
    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入；0=未归属",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


@register_event(CoreEvents.TTS_UTTERANCE_FINISHED)
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
        live_session_id: 场次主键（发布方不填，由场次盖章拦截器注入；0=未归属）。
        timestamp_ms: 事件发布时间戳（Unix 毫秒），用于日志/排序。
    """

    utterance_id: str = Field(..., description="一次发声实例的唯一 ID（与 started 事件对应）")
    engine: str = Field(..., description="TTS 引擎标识")
    duration_ms: int = Field(..., description="实际播放时长（Unix 毫秒，PCM 样本数÷采样率精确计算）")
    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入；0=未归属",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


@register_event(CoreEvents.TTS_UTTERANCE_FAILED)
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
        speech_text: 本次发声对应的文本内容（合成失败也必带原文，供字幕等
            消费者在无声音时照常显示文本）。
        engine: TTS 引擎标识。
        error_message: 失败原因描述（异常 message / 错误码 / 阶段标记），
            供编排层兜底决策与日志对账使用。
        live_session_id: 场次主键（发布方不填，由场次盖章拦截器注入；0=未归属）。
        timestamp_ms: 事件发布时间戳（Unix 毫秒），用于日志/排序。
    """

    utterance_id: str = Field(..., description="一次发声实例的唯一 ID（与 started 事件对应）")
    speech_text: str = Field(..., description="本次发声对应的文本内容（失败也必带原文）")
    engine: str = Field(..., description="TTS 引擎标识")
    error_message: str = Field(..., description="失败原因描述（异常 message / 错误码 / 阶段标记）")
    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入；0=未归属",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


@register_event(CoreEvents.TTS_UTTERANCE_DROPPED)
class UtteranceDroppedPayload(BasePayload):
    """一次发声被播放队列丢弃（确知未送到观众面前）事件 Payload。

    事件名：``tts.utterance.dropped`` —— 仅由播放队列在四条确知未出屏、未出声的路径发布：
    关停时清掉未轮到的队列条目、停机中拒绝入队、段落被更高优先级插断后丢掉剩余句、过期作废。
    存储记账器据此在对应台词行写上 ``dropped_at_ms``（没送达标记），排除出"主播说过的话"。
    """

    utterance_id: str = Field(..., description="一次发声实例的唯一 ID（与 streamer.speech 对应）")
    reason: str = Field(
        ..., description="丢弃原因（queue_stopped / stopping_rejected / segment_interrupted / expired）"
    )
    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入；0=未归属",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


@register_event(CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY)
class UtteranceSentenceBoundaryPayload(BasePayload):
    """播放音频越过一句发声后的静音间隙事件 Payload。

    事件名：``tts.utterance.sentence_boundary`` —— 播放路径边播边检测静音，
    检出 ≥ 阈值（约 200ms）的间隙且此前确有发声时发布。发布时刻 = 间隙在
    声卡上被写出的时刻，与观众听到的停顿天然同步。
    订阅者：字幕 follower（把上屏文本切到下一段）。
    终点广播，消费者不得触发新决策。

    Attributes:
        utterance_id: 一次发声实例的唯一 ID（与 started 事件对应）。
        segment_index: 本次发声内检出的第几个间隙（从 1 起递增）；只作日志
            对账辅助，消费方按"收到即切下一段"行事，不依赖该值。
        live_session_id: 场次主键（发布方不填，由场次盖章拦截器注入；0=未归属）。
        timestamp_ms: 事件发布时间戳（Unix 毫秒），用于日志/排序。
    """

    utterance_id: str = Field(..., description="一次发声实例的唯一 ID（与 started 事件对应）")
    segment_index: int = Field(..., ge=1, description="本次发声内检出的第几个句间隙（从 1 起递增）")
    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入；0=未归属",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


# ---------------------------------------------------------------------------
# 失败"已报告"标记
# ---------------------------------------------------------------------------

# 放在 failed 载荷旁边而不是 tts 包里：播放队列（主播 Agent 内部件）只依赖事件契约，
# 不为读一个标记把整个 tts 包（各引擎及其依赖）拉进导入链。
# 异常上的标记属性名。引擎发完 failed 事件再抛出异常时给异常打上它，
# 编排队列的兜底（speak 抛异常就补发 failed）看到标记就只计数打日志、不再补发，
# 否则同一句话会发出两条 failed，仪表盘失败句数与诊断记录都翻倍。
FAILURE_REPORTED_ATTR = "tts_failure_reported"


def mark_failure_reported(exc: BaseException) -> None:
    """标记"这次失败的 failed 事件引擎已经发布过"。

    触发条件：引擎在合成或播放中途失败、已经把 ``tts.utterance.failed``
    发出去，接着要把异常继续上抛给编排队列时。直接在异常对象上打属性，
    不换异常类型，上游按类型捕获异常的逻辑不受影响。

    只有"已确认发出"才该打标记：宁可多出一次字幕，也不能让一句话连字幕
    都没有，所以没打标记的异常（包括未知异常）队列一律照常补发。
    """
    setattr(exc, FAILURE_REPORTED_ATTR, True)


def is_failure_reported(exc: BaseException) -> bool:
    """异常是否带"引擎已发过 failed 事件"的标记；没有标记一律视为未报告。"""
    return getattr(exc, FAILURE_REPORTED_ATTR, False) is True


__all__ = [
    "UtteranceStartedPayload",
    "UtteranceFinishedPayload",
    "UtteranceFailedPayload",
    "UtteranceDroppedPayload",
    "UtteranceSentenceBoundaryPayload",
    "FAILURE_REPORTED_ATTR",
    "mark_failure_reported",
    "is_failure_reported",
]
