"""TTS 引擎共享函数模块

四个 TTS 引擎（Edge / GPTSoVITS / Omni / Voicebox）在播放时长计算与
utterance 生命周期事件发布上存在重复逻辑，本模块集中收纳。

提供的能力：

- ``compute_duration_ms``：从 PCM 样本数 + 采样率计算播放时长（毫秒）
- ``build_stats_dict``：统一的 Provider 状态统计 dict 构造
- ``emit_utterance_started`` / ``emit_utterance_finished`` /
  ``emit_utterance_failed``：TTS utterance 生命周期事件发布

设计约束：

- 函数级复用，不引入基类（Provider 执行模型差异大，强抽基类 = 抽象泄漏）
- TTS 引擎只发自己的 utterance 事件，不订阅任何事件
- 引擎 ``event_bus`` 为可选（测试 / 直调场景可传 None）；None 时静默跳过发布
- 事件发布走 CoreEvents 常量，不硬编码事件名字符串
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from src.modules.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus


# ---------------------------------------------------------------------------
# 播放时长计算
# ---------------------------------------------------------------------------


def compute_duration_ms(sample_count: int, sample_rate: int) -> int:
    """从 PCM 样本数 + 采样率计算播放时长（毫秒）。

    计算公式：``duration_ms = int(sample_count * 1000 / sample_rate)``，采样率
    非正数时返回 0（防御非法值）。

    Args:
        sample_count: PCM 样本数（一维 ndarray 的 len 即得；多声道请按"样本数
            = ndarray.size // 声道数"换算后再传入，保证时长与单声道一致）。
        sample_rate: 采样率（Hz）。

    Returns:
        int: 播放时长（毫秒）。
    """
    if sample_rate <= 0:
        return 0
    return int(sample_count * 1000 // sample_rate)


# ---------------------------------------------------------------------------
# Provider 状态统计
# ---------------------------------------------------------------------------


def build_stats_dict(
    *,
    name: str,
    is_connected: bool,
    render_count: int,
    error_count: int,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造统一的 Provider 状态统计 dict。

    四个 TTS Provider 的 ``get_stats()`` 输出字段名一致，便于上层做横向聚合。
    OmniTTS 多一个 ``buffer_size`` 字段，通过 ``extra`` 注入即可。

    Args:
        name: Provider 类名。
        is_connected: 是否已连接/初始化。
        render_count: 已渲染次数。
        error_count: 已出错次数。
        extra: 额外字段（如 buffer_size），键值直接并入结果 dict。

    Returns:
        Dict[str, Any]: 状态统计 dict。
    """
    stats: Dict[str, Any] = {
        "name": name,
        "is_connected": bool(is_connected),
        "render_count": render_count,
        "error_count": error_count,
    }
    if extra:
        stats.update(extra)
    return stats


# ---------------------------------------------------------------------------
# Utterance 生命周期事件发布
# ---------------------------------------------------------------------------


async def emit_utterance_started(
    event_bus: Optional["EventBus"],
    *,
    utterance_id: Optional[str],
    speech_text: str,
    engine: str,
    duration_ms: Optional[int],
) -> None:
    """发布 ``tts.utterance.started`` 事件。

    utterance_id 与 event_bus 任一为 None 时静默跳过（手动 / 直调场景无
    utterance 上下文，不发任何事件）。

    Args:
        event_bus: 事件总线（None 时静默跳过）。
        utterance_id: 一次发声实例的唯一 ID（None 时静默跳过）。
        speech_text: 本次发声对应的文本内容。
        engine: TTS 引擎标识（"edge" / "gptsovits" / "omni" / "voicebox"）。
        duration_ms: 预计播放时长（毫秒）；全量引擎为合成后精确值；流式
            引擎合成未完时为 None。
    """
    if event_bus is None or utterance_id is None:
        return
    payload = UtteranceStartedPayload(
        utterance_id=utterance_id,
        speech_text=speech_text,
        engine=engine,
        duration_ms=duration_ms,
        timestamp_ms=now_ms(),
    )
    await event_bus.emit(CoreEvents.TTS_UTTERANCE_STARTED, payload, source=engine)


async def emit_utterance_finished(
    event_bus: Optional["EventBus"],
    *,
    utterance_id: Optional[str],
    engine: str,
    duration_ms: int,
) -> None:
    """发布 ``tts.utterance.finished`` 事件。

    utterance_id 与 event_bus 任一为 None 时静默跳过（手动 / 直调场景无
    utterance 上下文，不发任何事件）。

    Args:
        event_bus: 事件总线（None 时静默跳过）。
        utterance_id: 一次发声实例的唯一 ID（None 时静默跳过）。
        engine: TTS 引擎标识。
        duration_ms: 实际播放时长（毫秒），由 PCM 样本数÷采样率精确计算。
    """
    if event_bus is None or utterance_id is None:
        return
    payload = UtteranceFinishedPayload(
        utterance_id=utterance_id,
        engine=engine,
        duration_ms=duration_ms,
        timestamp_ms=now_ms(),
    )
    await event_bus.emit(CoreEvents.TTS_UTTERANCE_FINISHED, payload, source=engine)


async def emit_utterance_failed(
    event_bus: Optional["EventBus"],
    *,
    utterance_id: Optional[str],
    engine: str,
    error_message: str,
) -> None:
    """发布 ``tts.utterance.failed`` 事件。

    utterance_id 与 event_bus 任一为 None 时静默跳过（手动 / 直调场景无
    utterance 上下文，不发任何事件）。

    Args:
        event_bus: 事件总线（None 时静默跳过）。
        utterance_id: 一次发声实例的唯一 ID（None 时静默跳过）。
        engine: TTS 引擎标识。
        error_message: 失败原因描述（异常 message / 错误码 / 阶段标记）。
    """
    if event_bus is None or utterance_id is None:
        return
    payload = UtteranceFailedPayload(
        utterance_id=utterance_id,
        engine=engine,
        error_message=error_message,
        timestamp_ms=now_ms(),
    )
    await event_bus.emit(CoreEvents.TTS_UTTERANCE_FAILED, payload, source=engine)


__all__ = [
    "compute_duration_ms",
    "build_stats_dict",
    "emit_utterance_started",
    "emit_utterance_finished",
    "emit_utterance_failed",
]
