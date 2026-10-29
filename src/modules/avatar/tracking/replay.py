"""面捕合成器离线回放：拿一段事件流水逐帧推进合成器，输出各通道曲线与指标

不需要 VTS 或任何平台：事件按时间戳喂给一个真实的运行时（假时钟、帧循环关闭），
每 33 毫秒推一帧并记下来。用来调参数、比较改动前后，以及回答几个可量的问题：

- 旧链路下表情比声音早多少（发言事件到开始播放的间隔）；
- 表情每分钟出现 / 切换几次；
- 说话与不说话时每分钟眨几次；
- 头和眼睛最长有多久完全不动。

事件来源是事件历史记录器落盘的 JSONL（每行含 ``event_name`` 与 ``data``）；合成器
只关心发言与 TTS 播放四种事件，其余事件跳过。
"""

from __future__ import annotations

import html
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, ValidationError

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)

from .channels import EYE_OPEN_L, GAZE_X, HEAD_PITCH, HEAD_ROLL, HEAD_YAW, Frame
from .config import TrackingConfig
from .runtime import TrackingRuntime

FRAME_MS = 33

_PAYLOADS: Dict[str, type[BaseModel]] = {
    CoreEvents.STREAMER_SPEECH: StreamerSpeechPayload,
    CoreEvents.TTS_UTTERANCE_STARTED: UtteranceStartedPayload,
    CoreEvents.TTS_UTTERANCE_FINISHED: UtteranceFinishedPayload,
    CoreEvents.TTS_UTTERANCE_FAILED: UtteranceFailedPayload,
}

# 曲线图里画哪些通道（画全部太挤）
PLOT_CHANNELS = ("head_yaw", "head_pitch", "gaze_x", "eye_open_l", "brow_l", "mouth_smile", "mouth_open")
# "头和眼睛完全不动"的判定：这几个通道每帧变化都小于阈值（真正冻住，不是慢速漫步的首尾）
_STILL_CHANNELS = (HEAD_YAW, HEAD_PITCH, HEAD_ROLL, EYE_OPEN_L, GAZE_X)
_STILL_EPSILON = 0.001


@dataclass(frozen=True)
class ReplayEvent:
    """一条可回放的事件：时刻、事件名、载荷。"""

    t_ms: int
    name: str
    payload: BaseModel


@dataclass
class ReplayResult:
    """回放产物：逐帧记录（时刻、帧、此刻主表情）、事件与指标。"""

    frames: List[Tuple[int, Frame, str]] = field(default_factory=list)
    events: List[ReplayEvent] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)


def load_events(lines: Iterable[str]) -> List[ReplayEvent]:
    """从事件历史 JSONL 行里挑出合成器关心的事件，按时间排序（坏行跳过）。"""
    events: List[ReplayEvent] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        name = record.get("event_name") or record.get("type")
        model = _PAYLOADS.get(name)
        if model is None:
            continue
        try:
            payload = model.model_validate(record.get("data") or {})
        except ValidationError:
            continue
        events.append(ReplayEvent(int(record.get("timestamp_ms") or payload.timestamp_ms), name, payload))
    events.sort(key=lambda e: e.t_ms)
    return events


def load_event_files(paths: Iterable[Path]) -> List[ReplayEvent]:
    events: List[ReplayEvent] = []
    for path in paths:
        events.extend(load_events(path.read_text(encoding="utf-8").splitlines()))
    events.sort(key=lambda e: e.t_ms)
    return events


class _Clock:
    def __init__(self, now_ms: int) -> None:
        self.now = now_ms

    def __call__(self) -> int:
        return self.now


async def replay(
    events: List[ReplayEvent], config: Optional[TrackingConfig] = None, *, seed: int = 0, tail_ms: int = 5000
) -> ReplayResult:
    """把事件逐条喂给运行时，同时每帧推进；返回逐帧记录与指标。"""
    result = ReplayResult(events=list(events))
    if not events:
        result.metrics = compute_metrics(result)
        return result
    clock = _Clock(events[0].t_ms)
    bus = EventBus()
    has_tts = any(e.name == CoreEvents.TTS_UTTERANCE_STARTED for e in events)
    runtime = TrackingRuntime(
        config or TrackingConfig(),
        event_bus=bus,
        tts_enabled=has_tts,
        clock=clock,
        rng=random.Random(seed),
        run_loop=False,
    )
    await runtime.start()
    pending = list(events)
    end_ms = events[-1].t_ms + tail_ms
    t = events[0].t_ms
    while t <= end_ms:
        while pending and pending[0].t_ms <= t:
            event = pending.pop(0)
            clock.now = event.t_ms
            await bus.emit(event.name, event.payload, source="replay")
            await bus.wait_until_drained()
        clock.now = t
        frame = await runtime.tick_once()
        result.frames.append((t, frame, runtime.current_emotion()))
        t += FRAME_MS
    await runtime.stop()
    await bus.cleanup()
    result.metrics = compute_metrics(result)
    return result


def _speaking_intervals(events: List[ReplayEvent]) -> List[Tuple[int, int]]:
    """每句话从开始播放到播完（或失败）的时间段。"""
    started: Dict[str, int] = {}
    intervals: List[Tuple[int, int]] = []
    for event in events:
        uid = getattr(event.payload, "utterance_id", "")
        if event.name == CoreEvents.TTS_UTTERANCE_STARTED:
            started[uid] = event.t_ms
        elif event.name in (CoreEvents.TTS_UTTERANCE_FINISHED, CoreEvents.TTS_UTTERANCE_FAILED) and uid in started:
            intervals.append((started.pop(uid), event.t_ms))
    return intervals


def _percentile(values: List[int], q: float) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def compute_metrics(result: ReplayResult) -> Dict[str, Any]:
    """从逐帧记录与事件算出调参用的几项指标。"""
    frames = result.frames
    duration_ms = (frames[-1][0] - frames[0][0]) if len(frames) > 1 else 0
    minutes = max(duration_ms / 60_000, 1e-9)

    speech_at = {e.payload.utterance_id: e.t_ms for e in result.events if e.name == CoreEvents.STREAMER_SPEECH}
    leads = [
        e.t_ms - speech_at[e.payload.utterance_id]
        for e in result.events
        if e.name == CoreEvents.TTS_UTTERANCE_STARTED and e.payload.utterance_id in speech_at
    ]

    changes = sum(1 for a, b in zip(frames, frames[1:], strict=False) if b[2] != a[2] and b[2] != "neutral")

    intervals = _speaking_intervals(result.events)

    def speaking(t: int) -> bool:
        return any(start <= t <= end for start, end in intervals)

    blinks = {"speaking": 0, "idle": 0}
    for a, b in zip(frames, frames[1:], strict=False):
        if a[1].get(EYE_OPEN_L, 1.0) >= 0.3 > b[1].get(EYE_OPEN_L, 1.0):
            blinks["speaking" if speaking(b[0]) else "idle"] += 1
    speaking_ms = sum(min(end, frames[-1][0]) - start for start, end in intervals) if frames else 0
    idle_ms = max(duration_ms - speaking_ms, 0)

    still_run = still_max = 0
    for a, b in zip(frames, frames[1:], strict=False):
        moved = any(abs(b[1].get(ch, 0.0) - a[1].get(ch, 0.0)) >= _STILL_EPSILON for ch in _STILL_CHANNELS)
        still_run = 0 if moved else still_run + FRAME_MS
        still_max = max(still_max, still_run)

    return {
        "duration_s": round(duration_ms / 1000, 1),
        "utterances": len(speech_at),
        "speech_to_playback_ms": {
            "p50": _percentile(leads, 0.5),
            "p90": _percentile(leads, 0.9),
            "max": max(leads) if leads else None,
        },
        "expression_changes_per_min": round(changes / minutes, 2),
        "blinks_per_min_speaking": round(blinks["speaking"] / max(speaking_ms / 60_000, 1e-9), 1)
        if speaking_ms
        else None,
        "blinks_per_min_idle": round(blinks["idle"] / max(idle_ms / 60_000, 1e-9), 1) if idle_ms else None,
        "longest_still_ms": still_max,
    }


def render_html(result: ReplayResult, *, title: str = "面捕合成器回放") -> str:
    """把逐帧记录画成一张自包含的 HTML（每个通道一行折线，竖线标出每句话开始播放）。"""
    frames = result.frames
    if not frames:
        return f"<html><meta charset='utf-8'><body><h1>{html.escape(title)}</h1><p>没有可回放的事件。</p></body></html>"
    t0, t1 = frames[0][0], frames[-1][0]
    width = max(800, min(6000, (t1 - t0) // 20))
    row_h, pad = 70, 4

    def x(t: int) -> float:
        return (t - t0) / max(t1 - t0, 1) * width

    rows: List[str] = []
    for index, channel in enumerate(PLOT_CHANNELS):
        values = [f[1].get(channel, 0.0) for f in frames]
        low, high = min(values), max(values)
        span = (high - low) or 1.0
        top = index * row_h
        points = " ".join(
            f"{x(t):.1f},{top + pad + (row_h - 2 * pad) * (1 - (v - low) / span):.1f}"
            for (t, _, _), v in zip(frames[::2], values[::2], strict=False)
        )
        rows.append(
            f"<text x='4' y='{top + 14}' font-size='12'>{channel}（{low:.2f}~{high:.2f}）</text>"
            f"<polyline fill='none' stroke='#3b6fd8' stroke-width='1' points='{points}'/>"
            f"<line x1='0' x2='{width}' y1='{top + row_h}' y2='{top + row_h}' stroke='#ddd'/>"
        )
    height = row_h * len(PLOT_CHANNELS)
    for event in result.events:
        if event.name == CoreEvents.TTS_UTTERANCE_STARTED:
            emotion = next(
                (
                    e.payload.emotion
                    for e in result.events
                    if e.name == CoreEvents.STREAMER_SPEECH and e.payload.utterance_id == event.payload.utterance_id
                ),
                "",
            )
            rows.append(
                f"<line x1='{x(event.t_ms):.1f}' x2='{x(event.t_ms):.1f}' y1='0' y2='{height}' stroke='#e07a5f' stroke-dasharray='3,3'/>"
                f"<text x='{x(event.t_ms) + 2:.1f}' y='{height - 4}' font-size='10' fill='#e07a5f'>{html.escape(emotion)}</text>"
            )
    metrics = html.escape(json.dumps(result.metrics, ensure_ascii=False, indent=2))
    return (
        "<html><head><meta charset='utf-8'><title>" + html.escape(title) + "</title></head><body>"
        f"<h1>{html.escape(title)}</h1><pre>{metrics}</pre>"
        f"<div style='overflow-x:auto'><svg width='{width}' height='{height}' xmlns='http://www.w3.org/2000/svg'>"
        + "".join(rows)
        + "</svg></div></body></html>"
    )


__all__ = ["ReplayEvent", "ReplayResult", "compute_metrics", "load_event_files", "load_events", "render_html", "replay"]
