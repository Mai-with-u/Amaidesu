"""TTS 引擎共享函数模块

四个 TTS 引擎（Edge / GPTSoVITS / Omni / Voicebox）在播放时长计算与
utterance 生命周期事件发布上存在重复逻辑，本模块集中收纳。

提供的能力：

- ``run_full_engine_speech``：全量引擎的发声入口（自治生命周期 + 规范化
  + 串行锁 + 编排骨架，四步前奏收拢）
- ``prepare_speech_text``：合成前奏的文本规范化与空文本早退判定
- ``synthesize_play_and_notify``：全量引擎的 utterance 编排主干（合成→
  started→播放→finished，含取消截断与失败事件）
- ``compute_duration_ms``：从 PCM 样本数 + 采样率计算播放时长（毫秒）
- ``build_stats_dict``：统一的 Provider 状态统计 dict 构造
- ``emit_utterance_started`` / ``emit_utterance_finished`` /
  ``emit_utterance_failed``：TTS utterance 生命周期事件发布

设计约束：

- 函数级复用，不引入基类（Provider 执行模型差异大，强抽基类 = 抽象泄漏）
- 编排骨架只收拢全量引擎（合成完拿完整 PCM 再播）的同构主干；流式引擎
  （gptsovits）的事件时序与截断语义本质不同，主干留在引擎内
- TTS 引擎只发自己的 utterance 事件，不订阅任何事件
- 引擎 ``event_bus`` 为可选（测试 / 直调场景可传 None）；None 时静默跳过发布
- 事件发布走 CoreEvents 常量，不硬编码事件名字符串
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Optional, Tuple

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from src.modules.utils.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus


# ---------------------------------------------------------------------------
# 文本规范化
# ---------------------------------------------------------------------------

# 保留集：中文、字母数字、空白、TTS 通用切分标点、英文构词符（撇号/连字符）。
# 保留集之外的一切符号（emoji/波浪号/数学符/装饰符/未知的未来符号）统一映射为
# 中文逗号——保留停顿边界且零维护：新符号自动成为停顿而非造成前后文粘连。
# 标点本身在各引擎音素化时不发音，语气由参考音频与切句边界承载。
_TTS_KEEP_PATTERN = re.compile(
    r"[^\u4e00-\u9fff"
    r"a-zA-Z0-9\s"
    r",.;?!、，。？！；：…"
    r"'\-]+"
)

# 映射产生的连续逗号合并为单个
_TTS_MULTI_COMMA = re.compile("，{2,}")


def normalize_tts_text(text: str) -> str:
    """引擎无关的合成文本规范化：保留集之外的符号一律映射为中文逗号。

    各 TTS 引擎对 emoji 等符号的处理不可控（忽略/怪读/导致前后文粘连），
    统一映射为停顿既保住语义边界又免维护符号表。各引擎在合成前调用，
    只作用于送合成的文本——utterance 事件的 ``speech_text`` 必须传规范化
    前原文（字幕等展示面不被清洗改写）。
    """
    text = _TTS_KEEP_PATTERN.sub("，", text)
    return _TTS_MULTI_COMMA.sub("，", text)


# ---------------------------------------------------------------------------
# Utterance 编排骨架
# ---------------------------------------------------------------------------


def prepare_speech_text(logger: Any, text: str) -> Optional[str]:
    """合成前奏的文本规范化与空文本早退判定。

    返回送合成的规范化文本；规范化后为空白时返回 None（调用方直接返回，
    不发任何事件、不计任何计数）。utterance 事件的 ``speech_text`` 由调用方
    另行携带规范化前原文，不经本函数。
    """
    final_text = normalize_tts_text(text)
    if not final_text.strip():
        logger.debug("文本规范化后为空，跳过渲染")
        return None
    return final_text


async def run_full_engine_speech(
    provider: Any,
    *,
    text: str,
    utterance_id: Optional[str],
    synthesize: Callable[[str], Awaitable[Tuple[Any, int]]],
) -> None:
    """全量引擎的发声入口：ensure_setup → 规范化 → 串行锁 → 编排骨架。

    引擎的 ``handle_speech`` 只剩"文本→音频怎么合成"的差异；本入口收拢
    之前在各引擎逐字重复的四步前奏。``synthesize`` 收规范化后的文本，
    返回 (PCM 数组, 采样率)。流式引擎（gptsovits）不走本入口——其事件
    时序与截断语义见 ``synthesize_play_and_notify`` 的设计约束。

    Args:
        provider: 引擎实例，需暴露 ``_has_started`` / ``setup()`` / ``logger``
            / ``tts_lock``（缺失时惰性创建）及编排骨架所需的成员。
        text: 要合成的原始文本（规范化前）。
        utterance_id: 一次发声实例的唯一 ID；None 时事件发布跳过。
        synthesize: "规范化文本→音频"钩子。
    """
    if not provider._has_started:
        await provider.setup()
    final_text = prepare_speech_text(provider.logger, text)
    if final_text is None:
        return
    if getattr(provider, "tts_lock", None) is None:
        provider.tts_lock = asyncio.Lock()
    async with provider.tts_lock:
        await synthesize_play_and_notify(
            provider,
            utterance_id=utterance_id,
            speech_text=text,
            synthesize=lambda: synthesize(final_text),
        )


async def synthesize_play_and_notify(
    provider: Any,
    *,
    utterance_id: Optional[str],
    speech_text: str,
    synthesize: Callable[[], Awaitable[Tuple[Any, int]]],
) -> None:
    """全量引擎的 utterance 编排主干；引擎只提供"文本→音频"一步。

    事件序列固定：started（含按 PCM 精确计算的时长）→ 播放 → finished。
    取消（编排队列看门狗超时）时已提交给声卡的播放必须显式截断，否则与下
    一条语音重叠；失败先记日志再发 failed 事件并向上抛，由编排队列兜底。
    utterance_id / event_bus 缺失时事件发布静默跳过（见 emit_* 系列），
    播放照常执行。

    Args:
        provider: 引擎实例，需暴露 ``event_bus`` / ``logger`` /
            ``audio_manager``（play_audio / stop_audio）/ ``render_count`` /
            ``error_count`` / ``PROVIDER_NAME``。
        utterance_id: 一次发声实例的唯一 ID。
        speech_text: 事件展示文本（规范化前原文，字幕按此展示）。
        synthesize: "文本→音频"钩子，返回 (PCM 数组, 采样率)；返回空数组
            时跳过播放与事件（视为无可播内容，非失败）。
    """
    engine = provider.PROVIDER_NAME
    try:
        audio_array, samplerate = await synthesize()
        if len(audio_array) == 0:
            provider.logger.warning("合成返回空音频，跳过播放")
            return
        duration_ms = compute_duration_ms(len(audio_array), samplerate)

        await emit_utterance_started(
            provider.event_bus,
            utterance_id=utterance_id,
            speech_text=speech_text,
            engine=engine,
            duration_ms=duration_ms,
        )

        await provider.audio_manager.play_audio(audio_array, samplerate=samplerate)
        provider.render_count += 1

        await emit_utterance_finished(
            provider.event_bus,
            utterance_id=utterance_id,
            engine=engine,
            duration_ms=duration_ms,
        )
    except asyncio.CancelledError:
        # 协程取消不会停止已提交给声卡的播放（声卡线程独立于事件循环），
        # 超时取消时必须显式截断，否则与下一条语音重叠
        if provider.audio_manager is not None:
            provider.audio_manager.stop_audio()
        provider.logger.warning(f"TTS 渲染被取消（已截断播放）: utterance_id={utterance_id}")
        raise
    except Exception as e:
        provider.error_count += 1
        provider.logger.exception(f"TTS 渲染失败: {e}")
        await emit_utterance_failed(
            provider.event_bus,
            utterance_id=utterance_id,
            speech_text=speech_text,
            engine=engine,
            error_message=f"{type(e).__name__}: {e}",
        )
        raise


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
        speech_text: 本次发声的展示文本（规范化前原文，字幕等消费方按此
            展示；与送合成的规范化文本可不同）。
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
    speech_text: str,
    engine: str,
    error_message: str,
) -> None:
    """发布 ``tts.utterance.failed`` 事件。

    utterance_id 与 event_bus 任一为 None 时静默跳过（手动 / 直调场景无
    utterance 上下文，不发任何事件）。

    Args:
        event_bus: 事件总线（None 时静默跳过）。
        utterance_id: 一次发声实例的唯一 ID（None 时静默跳过）。
        speech_text: 本次发声的展示文本（规范化前原文，合成失败也必带）。
        engine: TTS 引擎标识。
        error_message: 失败原因描述（异常 message / 错误码 / 阶段标记）。
    """
    if event_bus is None or utterance_id is None:
        return
    payload = UtteranceFailedPayload(
        utterance_id=utterance_id,
        speech_text=speech_text,
        engine=engine,
        error_message=error_message,
        timestamp_ms=now_ms(),
    )
    await event_bus.emit(CoreEvents.TTS_UTTERANCE_FAILED, payload, source=engine)


__all__ = [
    "run_full_engine_speech",
    "prepare_speech_text",
    "synthesize_play_and_notify",
    "compute_duration_ms",
    "build_stats_dict",
    "emit_utterance_started",
    "emit_utterance_finished",
    "emit_utterance_failed",
]
