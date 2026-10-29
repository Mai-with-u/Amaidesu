"""面捕合成器离线回放：从事件历史筛事件、逐帧回放、指标与曲线图"""

from __future__ import annotations

import json

from src.modules.avatar.tracking.config import TrackingConfig
from src.modules.avatar.tracking.replay import load_events, render_html, replay

T0 = 1_791_000_000_000


def _line(name: str, t: int, **data: object) -> str:
    return json.dumps({"event_name": name, "timestamp_ms": t, "data": {"timestamp_ms": t, **data}}, ensure_ascii=False)


def _session() -> list[str]:
    return [
        _line("tts.utterance.started", T0 + 600, utterance_id="u1", speech_text="你好呀", engine="fake"),
        _line("streamer.speech", T0, utterance_id="u1", text="你好呀", emotion="happy", emotion_intensity=0.8),
        _line("planner.decision", T0 + 100, round_id="r1"),  # 合成器不关心的事件跳过
        "{坏掉的一行",
        _line("tts.utterance.finished", T0 + 2600, utterance_id="u1", engine="fake", duration_ms=2000),
    ]


def test_load_events_keeps_only_tracking_events_sorted_by_time() -> None:
    events = load_events(_session())
    assert [e.name for e in events] == ["streamer.speech", "tts.utterance.started", "tts.utterance.finished"]


async def test_replay_reports_playback_lead_expression_and_draws_channels() -> None:
    config = TrackingConfig(gaze_enabled=False)
    result = await replay(load_events(_session()), config, seed=1)
    emotions = {emotion for _, _, emotion in result.frames}
    assert "happy" in emotions and result.frames[0][2] == "neutral"
    happy_from = next(t for t, _, emotion in result.frames if emotion == "happy")
    assert happy_from >= T0 + 600  # 表情从开始播放起，不在发言事件那一刻
    metrics = result.metrics
    assert metrics["speech_to_playback_ms"]["p50"] == 600  # 旧链路的提前量
    assert metrics["utterances"] == 1 and metrics["expression_changes_per_min"] > 0

    page = render_html(result)
    assert "<svg" in page and "mouth_smile" in page and "happy" in page


async def test_replay_of_nothing_is_empty_but_renders() -> None:
    result = await replay([])
    assert result.frames == [] and "没有可回放的事件" in render_html(result)
