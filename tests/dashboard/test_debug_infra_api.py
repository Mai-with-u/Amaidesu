"""调试面基础设施直测 API 测试套件

覆盖 ``/api/v1/debug/subtitle|tts`` 五个端点：

1. 字幕 test / clear / status — 服务未注入 503、正常路径（Fake 服务记录
   调用与 utterance_id 前缀）、show / clear 抛异常回 success=false。
2. TTS test / status — 引擎未装配 503、handle_speech 成功与异常回传、
   get_stats 透传。

注：使用 Fake 字幕服务 / TTS 引擎（鸭子类型），不触碰真实 Tk GUI 与
音频设备；server 经构造器注入真实 DashboardServer 后绑定依赖。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fake 服务（鸭子类型：实现调试端点关心的门面即可）
# ---------------------------------------------------------------------------


class FakeSubtitleService:
    """最小字幕服务替身：记录 show / clear，诊断清单固定两项。"""

    def __init__(
        self,
        *,
        show_exception: Optional[Exception] = None,
        clear_exception: Optional[Exception] = None,
    ) -> None:
        self.show_calls: List[Dict[str, Any]] = []
        self.clear_calls: int = 0
        self._show_exception = show_exception
        self._clear_exception = clear_exception

    @property
    def backend_count(self) -> int:
        return 2

    @property
    def backend_diagnostics(self) -> List[Dict[str, Any]]:
        return [
            {"name": "TkGuiBackend", "enabled": True},
            {"name": "DashboardBackend", "enabled": False},
        ]

    async def show(self, text: str, utterance_id: Optional[str] = None) -> None:
        self.show_calls.append({"text": text, "utterance_id": utterance_id})
        if self._show_exception is not None:
            raise self._show_exception

    async def clear(self) -> None:
        self.clear_calls += 1
        if self._clear_exception is not None:
            raise self._clear_exception


class FakeTtsEngine:
    """最小 TTS 引擎替身：记录 handle_speech，get_stats 返回固定统计。"""

    def __init__(self, *, speech_exception: Optional[Exception] = None) -> None:
        self.speech_calls: List[Dict[str, Any]] = []
        self._speech_exception = speech_exception

    async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
        self.speech_calls.append({"text": text, "utterance_id": utterance_id})
        if self._speech_exception is not None:
            raise self._speech_exception

    def get_stats(self) -> Dict[str, Any]:
        return {"name": "FakeTTS", "is_connected": True, "render_count": 1, "error_count": 0}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_config(config_dir: Path) -> None:
    """七文件基线（ConfigService.initialize 需要）。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(config_dir)


def _make_client(
    config_dir: Path,
    *,
    subtitle_service: Optional[FakeSubtitleService] = None,
    tts_engine: Optional[FakeTtsEngine] = None,
) -> TestClient:
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.config.service import ConfigService
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    svc = ConfigService(base_dir=str(config_dir.parent))
    svc.initialize()

    server = DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
        subtitle_service=subtitle_service,
        tts_engine=tts_engine,
    )
    set_dashboard_server(server)
    return TestClient(create_app())


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    return cfg


# ---------------------------------------------------------------------------
# 字幕直测端点
# ---------------------------------------------------------------------------


def test_subtitle_test_503_when_service_missing(config_dir: Path) -> None:
    """字幕服务未注入（极简启动）→ 503，detail 指明缺什么。"""
    _write_config(config_dir)
    client = _make_client(config_dir, subtitle_service=None)

    resp = client.post("/api/v1/debug/subtitle/test", json={"text": "测试"})
    assert resp.status_code == 503
    assert "字幕服务" in str(resp.json().get("detail", ""))


def test_subtitle_test_broadcasts_with_debug_prefix(config_dir: Path) -> None:
    """正常路径：200 + success=true + backend_count；utterance_id 带 debug-test 前缀。"""
    _write_config(config_dir)
    service = FakeSubtitleService()
    client = _make_client(config_dir, subtitle_service=service)

    resp = client.post("/api/v1/debug/subtitle/test", json={"text": "你好，字幕测试"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["backend_count"] == 2
    assert body["error"] is None

    assert len(service.show_calls) == 1
    call = service.show_calls[0]
    assert call["text"] == "你好，字幕测试"
    assert isinstance(call["utterance_id"], str) and call["utterance_id"].startswith("debug-test-")


def test_subtitle_test_show_exception_returns_success_false(config_dir: Path) -> None:
    """show 抛异常 → 200 + success=false + error 如实回传（非 500）。"""
    _write_config(config_dir)
    service = FakeSubtitleService(show_exception=RuntimeError("广播链炸了"))
    client = _make_client(config_dir, subtitle_service=service)

    resp = client.post("/api/v1/debug/subtitle/test", json={"text": "测试"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "广播链炸了" in body["error"]


def test_subtitle_clear_calls_service(config_dir: Path) -> None:
    """清空端点调用 service.clear。"""
    _write_config(config_dir)
    service = FakeSubtitleService()
    client = _make_client(config_dir, subtitle_service=service)

    resp = client.post("/api/v1/debug/subtitle/clear")
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "error": None}
    assert service.clear_calls == 1


def test_subtitle_clear_exception_returns_success_false(config_dir: Path) -> None:
    """clear 抛异常 → 200 + success=false。"""
    _write_config(config_dir)
    service = FakeSubtitleService(clear_exception=RuntimeError("清空失败"))
    client = _make_client(config_dir, subtitle_service=service)

    resp = client.post("/api/v1/debug/subtitle/clear")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "清空失败" in body["error"]


def test_subtitle_status_returns_backend_inventory(config_dir: Path) -> None:
    """status 端点回传后端清单（name + enabled），含降级后端。"""
    _write_config(config_dir)
    service = FakeSubtitleService()
    client = _make_client(config_dir, subtitle_service=service)

    resp = client.get("/api/v1/debug/subtitle/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["backend_count"] == 2
    assert body["backends"] == [
        {"name": "TkGuiBackend", "enabled": True},
        {"name": "DashboardBackend", "enabled": False},
    ]


def test_subtitle_endpoints_503_when_missing(config_dir: Path) -> None:
    """status / clear 在服务未注入时同样 503。"""
    _write_config(config_dir)
    client = _make_client(config_dir, subtitle_service=None)

    assert client.get("/api/v1/debug/subtitle/status").status_code == 503
    assert client.post("/api/v1/debug/subtitle/clear").status_code == 503


# ---------------------------------------------------------------------------
# TTS 直测端点
# ---------------------------------------------------------------------------


def test_tts_test_503_when_engine_missing(config_dir: Path) -> None:
    """TTS 引擎未装配（未配置 TTS）→ 503。"""
    _write_config(config_dir)
    client = _make_client(config_dir, tts_engine=None)

    resp = client.post("/api/v1/debug/tts/test", json={"text": "试说"})
    assert resp.status_code == 503
    assert "TTS" in str(resp.json().get("detail", ""))


def test_tts_test_calls_handle_speech_with_debug_prefix(config_dir: Path) -> None:
    """正常路径：handle_speech 被调用，utterance_id 带 debug-test 前缀。"""
    _write_config(config_dir)
    engine = FakeTtsEngine()
    client = _make_client(config_dir, tts_engine=engine)

    resp = client.post("/api/v1/debug/tts/test", json={"text": "大家好"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["error"] is None

    assert len(engine.speech_calls) == 1
    call = engine.speech_calls[0]
    assert call["text"] == "大家好"
    assert isinstance(call["utterance_id"], str) and call["utterance_id"].startswith("debug-test-")
    assert body["utterance_id"] == call["utterance_id"]


def test_tts_test_synthesis_failure_returns_success_false(config_dir: Path) -> None:
    """合成失败（引擎协议约定：失败已发 tts.utterance.failed）→ success=false + error。"""
    _write_config(config_dir)
    engine = FakeTtsEngine(speech_exception=RuntimeError("TTS 服务不可达"))
    client = _make_client(config_dir, tts_engine=engine)

    resp = client.post("/api/v1/debug/tts/test", json={"text": "试说"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "TTS 服务不可达" in body["error"]
    assert body["utterance_id"] is not None


def test_tts_status_passthrough_stats(config_dir: Path) -> None:
    """status 端点透传 get_stats 字段。"""
    _write_config(config_dir)
    client = _make_client(config_dir, tts_engine=FakeTtsEngine())

    resp = client.get("/api/v1/debug/tts/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["stats"]["name"] == "FakeTTS"
    assert body["stats"]["is_connected"] is True


def test_tts_status_503_when_engine_missing(config_dir: Path) -> None:
    """引擎未装配时 status 同样 503。"""
    _write_config(config_dir)
    client = _make_client(config_dir, tts_engine=None)

    assert client.get("/api/v1/debug/tts/status").status_code == 503
