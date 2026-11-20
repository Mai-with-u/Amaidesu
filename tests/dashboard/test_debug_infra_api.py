"""调试面基础设施直测 API（/api/v1/debug/subtitle|tts）：服务缺失 503、成功路径、异常回 success=false

使用鸭子类型的字幕服务 / TTS 引擎替身，不触碰真实 Tk GUI 与音频设备。
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any, Dict, List, Optional

import pytest

from src.modules.dashboard.api.debug import _UtteranceTimeline
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import (
    UtteranceFinishedPayload,
    UtterancePlaybackStartedPayload,
)


class FakeEventBus:
    """订阅退订记账 + 暂存 handler；测试直接调用 handler 模拟事件到达。"""

    def __init__(self) -> None:
        self.registered: Dict[str, Any] = {}
        self.unregistered: List[str] = []

    def on(self, event_name: str, handler: Any, model_class: Any) -> None:
        self.registered[event_name] = handler

    def off(self, event_name: str, handler: Any) -> None:
        self.registered.pop(event_name, None)
        self.unregistered.append(event_name)

    async def emit(self, event_name: str, payload: Any, source: str = "") -> None:
        handler = self.registered.get(event_name)
        if handler is not None:
            await handler(event_name, payload, source)


class FakeSubtitleService:
    def __init__(self, show_exception: Optional[Exception] = None, clear_exception: Optional[Exception] = None) -> None:
        self.show_calls: List[Dict[str, Any]] = []
        self.clear_calls = 0
        self._show_exception = show_exception
        self._clear_exception = clear_exception

    @property
    def backend_count(self) -> int:
        return 2

    @property
    def backend_diagnostics(self) -> List[Dict[str, Any]]:
        return [{"name": "TkGuiBackend", "enabled": True}, {"name": "DashboardBackend", "enabled": False}]

    async def show(self, text: str, utterance_id: Optional[str] = None) -> None:
        self.show_calls.append({"text": text, "utterance_id": utterance_id})
        if self._show_exception is not None:
            raise self._show_exception

    async def clear(self) -> None:
        self.clear_calls += 1
        if self._clear_exception is not None:
            raise self._clear_exception


class FakeTtsEngine:
    def __init__(
        self,
        speech_exception: Optional[Exception] = None,
        synth_exception: Optional[Exception] = None,
        event_bus: Optional[FakeEventBus] = None,
    ) -> None:
        self.speech_calls: List[Dict[str, Any]] = []
        self.synth_calls: List[Dict[str, Any]] = []
        self._speech_exception = speech_exception
        self._synth_exception = synth_exception
        self.event_bus = event_bus
        self.tts_lock = asyncio.Lock()

    async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
        self.speech_calls.append({"text": text, "utterance_id": utterance_id})
        if self._speech_exception is not None:
            raise self._speech_exception
        # 模拟流式引擎锚点时序：首块出声（playback_started）→ 播放中 → 播完（finished）
        if self.event_bus is not None and utterance_id:
            await self.event_bus.emit(
                CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED,
                UtterancePlaybackStartedPayload(utterance_id=utterance_id),
            )
            await asyncio.sleep(0.01)
            await self.event_bus.emit(
                CoreEvents.TTS_UTTERANCE_FINISHED,
                UtteranceFinishedPayload(utterance_id=utterance_id, engine="fake", duration_ms=10),
            )

    async def debug_synthesize(self, text: str, overrides: Optional[Dict[str, Any]] = None) -> bytes:
        self.synth_calls.append({"text": text, "overrides": overrides or {}})
        if self._synth_exception is not None:
            raise self._synth_exception
        return b"RIFF-fake-audio"

    def get_stats(self) -> Dict[str, Any]:
        stats: Dict[str, Any] = {"name": "FakeTTS", "is_connected": True, "render_count": 1, "error_count": 0}
        stats.update(getattr(self, "stats_extra", {}))
        return stats


class _MinimalEngine:
    """非 GSV 后端：没有 debug_synthesize。"""

    async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
        pass

    def get_stats(self) -> Dict[str, Any]:
        return {"name": "Minimal"}


@pytest.mark.parametrize(
    ("method", "path", "detail"),
    [
        ("post", "/api/v1/debug/subtitle/test", "字幕服务"),
        ("post", "/api/v1/debug/subtitle/clear", ""),
        ("get", "/api/v1/debug/subtitle/status", ""),
        ("post", "/api/v1/debug/tts/test", "TTS"),
        ("post", "/api/v1/debug/tts/say", "TTS"),
        ("get", "/api/v1/debug/tts/status", ""),
    ],
)
def test_endpoints_return_503_when_service_missing(mount, method: str, path: str, detail: str) -> None:
    kwargs = {"json": {"text": "测试"}} if path.endswith(("/test", "/say")) else {}
    resp = getattr(mount(), method)(path, **kwargs)
    assert resp.status_code == 503 and detail in str(resp.json().get("detail", ""))


def test_subtitle_test_clear_and_status(mount) -> None:
    service = FakeSubtitleService()
    client = mount(subtitle_service=service)

    body = client.post("/api/v1/debug/subtitle/test", json={"text": "你好，字幕测试"}).json()
    assert (body["success"], body["backend_count"], body["error"]) == (True, 2, None)
    (call,) = service.show_calls
    assert call["text"] == "你好，字幕测试" and call["utterance_id"].startswith("debug-test-")

    assert client.post("/api/v1/debug/subtitle/clear").json() == {"success": True, "error": None}
    assert service.clear_calls == 1
    status = client.get("/api/v1/debug/subtitle/status").json()
    assert (status["available"], status["backend_count"]) == (True, 2)
    assert status["backends"] == service.backend_diagnostics  # 含降级后端


@pytest.mark.parametrize(
    ("service", "path", "message"),
    [
        (FakeSubtitleService(show_exception=RuntimeError("广播链炸了")), "/api/v1/debug/subtitle/test", "广播链炸了"),
        (FakeSubtitleService(clear_exception=RuntimeError("清空失败")), "/api/v1/debug/subtitle/clear", "清空失败"),
    ],
    ids=["show", "clear"],
)
def test_subtitle_exceptions_return_success_false_not_500(mount, service, path: str, message: str) -> None:
    resp = mount(subtitle_service=service).post(path, json={"text": "测试"})
    assert resp.status_code == 200 and resp.json()["success"] is False and message in resp.json()["error"]


def test_tts_test_speaks_on_device_with_debug_prefix_and_reports_failure(mount) -> None:
    engine = FakeTtsEngine()
    body = mount(tts_engine=engine).post("/api/v1/debug/tts/test", json={"text": "大家好"}).json()
    assert (body["success"], body["error"]) == (True, None)
    (call,) = engine.speech_calls
    assert call["text"] == "大家好" and call["utterance_id"].startswith("debug-test-")
    assert body["utterance_id"] == call["utterance_id"]

    # 合成失败（引擎协议约定失败已发 tts.utterance.failed）→ success=false + error
    failing = mount(tts_engine=FakeTtsEngine(speech_exception=RuntimeError("TTS 服务不可达")))
    body = failing.post("/api/v1/debug/tts/test", json={"text": "试说"}).json()
    assert body["success"] is False and "TTS 服务不可达" in body["error"] and body["utterance_id"] is not None


def test_tts_audio_mode_returns_base64_and_filters_unset_overrides(mount) -> None:
    engine = FakeTtsEngine()
    client = mount(tts_engine=engine)
    overrides = {"temperature": 1.0, "speed_factor": 1.0, "sample_steps": 8, "top_k": 20, "top_p": 0.6}

    body = client.post("/api/v1/debug/tts/test", json={"text": "试听文本", "play_on_device": False, **overrides}).json()
    assert body["success"] is True and base64.b64decode(body["audio_base64"]) == b"RIFF-fake-audio"
    assert engine.synth_calls == [{"text": "试听文本", "overrides": overrides}]

    # 未填的覆盖参数（None）不透传，用引擎配置值
    client.post("/api/v1/debug/tts/test", json={"text": "试听", "play_on_device": False})
    assert engine.synth_calls[-1]["overrides"] == {}


@pytest.mark.parametrize(
    ("engine", "message"),
    [
        (_MinimalEngine(), "不支持音频合成"),
        (FakeTtsEngine(synth_exception=RuntimeError("GSV 服务不可达")), "GSV 服务不可达"),
    ],
    ids=["no_synthesize", "synthesis_failed"],
)
def test_tts_audio_mode_failures(mount, engine, message: str) -> None:
    resp = mount(tts_engine=engine).post("/api/v1/debug/tts/test", json={"text": "试听", "play_on_device": False})
    assert resp.status_code == 200 and resp.json()["success"] is False and message in resp.json()["error"]


def test_tts_test_reports_elapsed_ms_in_both_modes(mount) -> None:
    """整场回放的耗时统计面：播放/合成两模式与各失败路径都带 elapsed_ms（毫秒）。"""
    client = mount(tts_engine=FakeTtsEngine())

    # 播放模式：服务端处理总耗时（含播放）；无事件总线时时间线分段缺席
    played = client.post("/api/v1/debug/tts/test", json={"text": "播放一句"}).json()
    assert isinstance(played["elapsed_ms"], int)
    assert played["first_audio_ms"] is None and played["playback_ms"] is None

    # 合成模式：纯合成耗时
    synthesized = client.post("/api/v1/debug/tts/test", json={"text": "合成一句", "play_on_device": False}).json()
    assert isinstance(synthesized["elapsed_ms"], int)

    # 失败路径同样带耗时（计到失败点）
    failing_play = mount(tts_engine=FakeTtsEngine(speech_exception=RuntimeError("引擎挂了")))
    body = failing_play.post("/api/v1/debug/tts/test", json={"text": "试说"}).json()
    assert isinstance(body["elapsed_ms"], int)

    failing_synth = mount(tts_engine=FakeTtsEngine(synth_exception=RuntimeError("合成挂了")))
    body = failing_synth.post("/api/v1/debug/tts/test", json={"text": "试听", "play_on_device": False}).json()
    assert isinstance(body["elapsed_ms"], int)

    unsupported = mount(tts_engine=_MinimalEngine())
    body = unsupported.post("/api/v1/debug/tts/test", json={"text": "试听", "play_on_device": False}).json()
    assert isinstance(body["elapsed_ms"], int)


def test_tts_test_reports_timeline_segments_with_event_bus(mount) -> None:
    """播放模式时间线分段：引擎发锚点事件时响应带首声延迟与出声期，且订阅随请求退订。"""
    bus = FakeEventBus()
    engine = FakeTtsEngine(event_bus=bus)
    body = mount(tts_engine=engine, event_bus=bus).post("/api/v1/debug/tts/test", json={"text": "你好"}).json()
    assert body["success"] is True
    assert body["first_audio_ms"] is not None and body["first_audio_ms"] >= 0
    assert body["playback_ms"] is not None and body["playback_ms"] >= 5
    assert bus.registered == {}


async def test_utterance_timeline_records_segment_spans() -> None:
    """时间线记录器：锚点事件按 utterance_id 记到达时刻，分段以请求起点为原点；stop 成对退订。"""
    bus = FakeEventBus()
    timeline = _UtteranceTimeline(bus)
    timeline.start()
    assert CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED in bus.registered
    assert CoreEvents.TTS_UTTERANCE_FINISHED in bus.registered

    playback_handler = bus.registered[CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED]
    finished_handler = bus.registered[CoreEvents.TTS_UTTERANCE_FINISHED]

    await playback_handler("", UtterancePlaybackStartedPayload(utterance_id="u1"), "")
    await asyncio.sleep(0.02)
    await finished_handler("", UtteranceFinishedPayload(utterance_id="u1", engine="fake", duration_ms=20), "")

    first_audio_ms, playback_ms = timeline.spans_for("u1")
    assert first_audio_ms is not None and first_audio_ms >= 0
    assert playback_ms is not None and playback_ms >= 10

    # 锚点不全的句子两段都按缺失处理
    assert timeline.spans_for("u2") == (None, None)
    assert timeline.spans_for(None) == (None, None)

    timeline.stop()
    assert bus.registered == {}
    assert set(bus.unregistered) == {CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED, CoreEvents.TTS_UTTERANCE_FINISHED}


def test_tts_test_honors_client_utterance_id(mount) -> None:
    """整场回放透传前端指定的 utterance_id（关联 WS 实时进度事件用）。"""
    engine = FakeTtsEngine()
    body = (
        mount(tts_engine=engine)
        .post("/api/v1/debug/tts/test", json={"text": "你好", "utterance_id": "replay-42-7"})
        .json()
    )
    assert body["success"] is True and body["utterance_id"] == "replay-42-7"
    assert engine.speech_calls[0]["utterance_id"] == "replay-42-7"


def test_tts_say_replays_text_with_say_prefix_and_reports_failure(mount) -> None:
    """台前重播：同文本重合成（走 handle_speech 完整链路），失败回 success=false。"""
    engine = FakeTtsEngine()
    body = mount(tts_engine=engine).post("/api/v1/debug/tts/say", json={"text": "重播这句"}).json()
    assert (body["success"], body["error"]) == (True, None)
    (call,) = engine.speech_calls
    assert call["text"] == "重播这句" and call["utterance_id"].startswith("debug-say-")
    assert body["utterance_id"] == call["utterance_id"]

    failing = mount(tts_engine=FakeTtsEngine(speech_exception=RuntimeError("TTS 服务不可达")))
    body = failing.post("/api/v1/debug/tts/say", json={"text": "重播"}).json()
    assert body["success"] is False and "TTS 服务不可达" in body["error"] and body["utterance_id"] is not None


def test_tts_status_passes_engine_stats_through(mount) -> None:
    body = mount(tts_engine=FakeTtsEngine()).get("/api/v1/debug/tts/status").json()
    assert body["available"] is True and body["stats"]["name"] == "FakeTTS" and body["stats"]["is_connected"] is True


def test_tts_status_extracts_debug_params_from_stats(mount) -> None:
    """引擎上报的调试参数描述表被摘出 stats 单独下发（前端按此渲染参数区）。"""
    engine = FakeTtsEngine()
    engine.stats_extra = {
        "debug_params": [
            {"key": "temperature", "label": "温度", "min": 0, "max": 2, "step": 0.1, "precision": 1, "default": 1.0}
        ]
    }
    body = mount(tts_engine=engine).get("/api/v1/debug/tts/status").json()
    assert body["debug_params"] == [
        {"key": "temperature", "label": "温度", "min": 0.0, "max": 2.0, "step": 0.1, "precision": 1, "default": 1.0}
    ]
    assert "debug_params" not in body["stats"]


@pytest.mark.parametrize(
    "raw",
    [
        "not-a-list",
        [{"key": "temperature"}],  # 缺必填字段
        ["not-a-dict"],
    ],
    ids=["not_list", "missing_fields", "not_dict_items"],
)
def test_tts_status_invalid_debug_params_hidden(mount, raw: Any) -> None:
    """描述表不合规时 debug_params 置 None（参数区隐藏），其余 stats 照常透传。"""
    engine = FakeTtsEngine()
    engine.stats_extra = {"debug_params": raw}
    body = mount(tts_engine=engine).get("/api/v1/debug/tts/status").json()
    assert body["debug_params"] is None and body["stats"]["name"] == "FakeTTS"


def test_tts_status_engine_without_debug_params_returns_none(mount) -> None:
    body = mount(tts_engine=FakeTtsEngine()).get("/api/v1/debug/tts/status").json()
    assert body["debug_params"] is None
