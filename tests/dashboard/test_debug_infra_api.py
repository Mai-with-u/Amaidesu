"""调试面基础设施直测 API（/api/v1/debug/subtitle|tts）：服务缺失 503、成功路径、异常回 success=false

使用鸭子类型的字幕服务 / TTS 引擎替身，不触碰真实 Tk GUI 与音频设备。
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any, Dict, List, Optional

import pytest


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
        self, speech_exception: Optional[Exception] = None, synth_exception: Optional[Exception] = None
    ) -> None:
        self.speech_calls: List[Dict[str, Any]] = []
        self.synth_calls: List[Dict[str, Any]] = []
        self._speech_exception = speech_exception
        self._synth_exception = synth_exception
        self.tts_lock = asyncio.Lock()

    async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
        self.speech_calls.append({"text": text, "utterance_id": utterance_id})
        if self._speech_exception is not None:
            raise self._speech_exception

    async def debug_synthesize(self, text: str, overrides: Optional[Dict[str, Any]] = None) -> bytes:
        self.synth_calls.append({"text": text, "overrides": overrides or {}})
        if self._synth_exception is not None:
            raise self._synth_exception
        return b"RIFF-fake-audio"

    def get_stats(self) -> Dict[str, Any]:
        return {"name": "FakeTTS", "is_connected": True, "render_count": 1, "error_count": 0}


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
        ("get", "/api/v1/debug/tts/status", ""),
    ],
)
def test_endpoints_return_503_when_service_missing(mount, method: str, path: str, detail: str) -> None:
    kwargs = {"json": {"text": "测试"}} if path.endswith("/test") else {}
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


def test_tts_status_passes_engine_stats_through(mount) -> None:
    body = mount(tts_engine=FakeTtsEngine()).get("/api/v1/debug/tts/status").json()
    assert body["available"] is True and body["stats"]["name"] == "FakeTTS" and body["stats"]["is_connected"] is True
