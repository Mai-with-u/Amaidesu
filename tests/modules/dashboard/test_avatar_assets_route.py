"""表情差分图小部件路由：纯文件名校验（防路径穿越）、未配置 404、正常取图、此刻该显示哪张"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.dashboard.api.avatar import router
from src.modules.dashboard.dependencies import set_dashboard_server
from src.modules.avatar.platform.expression_sheet.expression_sheet_provider import (
    ExpressionSheetProvider,
)

_PNG_BYTES = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(autouse=True)
def _reset_dashboard_server() -> Iterator[None]:
    """服务器单例是模块级全局，测完复位避免泄漏到其他测试。"""
    yield
    set_dashboard_server(None)


class _StubRegistry:
    """只登记了一个预置 Provider（或什么都没登记）的最小注册表桩。"""

    def __init__(self, provider: object | None) -> None:
        self._provider = provider

    def providers(self) -> list[object]:
        return [self._provider] if self._provider is not None else []


class _StubTracking:
    def __init__(self, emotion: str) -> None:
        self.emotion = emotion

    def current_emotion(self) -> str:
        return self.emotion


class _StubServer:
    def __init__(self, registry: _StubRegistry | None) -> None:
        self.tool_registry = registry


@pytest.fixture()
def sheet_dir(tmp_path: Path) -> Path:
    (tmp_path / "happy.png").write_bytes(_PNG_BYTES)
    (tmp_path / "neutral.png").write_bytes(_PNG_BYTES)
    (tmp_path / "secret.txt").write_bytes(b"top secret")
    return tmp_path


def _client(provider: object | None, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/avatar")
    server = _StubServer(_StubRegistry(provider))
    set_dashboard_server(server)
    return TestClient(app)


def test_serves_image_from_resolved_image_dir(sheet_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ExpressionSheetProvider(config={"image_dir": str(sheet_dir)})
    client = _client(provider, monkeypatch)
    resp = client.get("/api/v1/avatar/expression_sheet/assets/happy.png")
    assert resp.status_code == 200
    assert resp.content == _PNG_BYTES


def test_missing_file_returns_404(sheet_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ExpressionSheetProvider(config={"image_dir": str(sheet_dir)})
    client = _client(provider, monkeypatch)
    assert client.get("/api/v1/avatar/expression_sheet/assets/angry.png").status_code == 404


@pytest.mark.parametrize(
    "filename",
    [
        "../secret.txt",  # 父目录穿越
        "..\\secret.txt",  # Windows 反斜杠穿越
        "a/b.png",  # 路径分隔符
        "C:happy.png",  # Windows 盘符前缀
        "..",  # 纯父目录引用
    ],
)
def test_path_traversal_and_non_pure_filenames_are_rejected(
    sheet_dir: Path, monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    provider = ExpressionSheetProvider(config={"image_dir": str(sheet_dir)})
    client = _client(provider, monkeypatch)
    resp = client.get(f"/api/v1/avatar/expression_sheet/assets/{filename}")
    assert resp.status_code == 404
    assert b"top secret" not in resp.content


def test_non_image_file_inside_dir_is_not_served(sheet_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ExpressionSheetProvider(config={"image_dir": str(sheet_dir)})
    client = _client(provider, monkeypatch)
    resp = client.get("/api/v1/avatar/expression_sheet/assets/secret.txt")
    assert resp.status_code == 404
    assert b"top secret" not in resp.content


def test_unconfigured_provider_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ExpressionSheetProvider(config={})  # 未配置 image_dir
    client = _client(provider, monkeypatch)
    resp = client.get("/api/v1/avatar/expression_sheet/assets/neutral.png")
    assert resp.status_code == 404
    assert "image_dir" in resp.json()["detail"]


def test_missing_registry_or_provider_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(None, monkeypatch)  # Provider 未装配（降级登记 0 工具）
    assert client.get("/api/v1/avatar/expression_sheet/assets/neutral.png").status_code == 404

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/avatar")
    set_dashboard_server(_StubServer(None))  # type: ignore[arg-type]
    assert TestClient(app).get("/api/v1/avatar/expression_sheet/assets/neutral.png").status_code == 404


def test_state_follows_tracking_emotion_with_neutral_fallback(sheet_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracking = _StubTracking("happy")
    provider = ExpressionSheetProvider(config={"image_dir": str(sheet_dir)}, tracking=tracking)
    client = _client(provider, monkeypatch)
    assert client.get("/api/v1/avatar/expression_sheet/state").json() == {
        "emotion": "happy",
        "image_url": "/api/v1/avatar/expression_sheet/assets/happy.png",
    }
    tracking.emotion = "angry"  # 目录里没有 angry.png
    body = client.get("/api/v1/avatar/expression_sheet/state").json()
    assert body["image_url"] == "/api/v1/avatar/expression_sheet/assets/neutral.png"


def test_state_is_404_when_not_enabled_or_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _client(None, monkeypatch).get("/api/v1/avatar/expression_sheet/state").status_code == 404
    unconfigured = ExpressionSheetProvider(config={})
    assert _client(unconfigured, monkeypatch).get("/api/v1/avatar/expression_sheet/state").status_code == 404
