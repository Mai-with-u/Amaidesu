"""Vision Dashboard API：显示器枚举与预览端点

多数用例走真实 MssScreenCapture（本机 mss 可用）；失败路径用 monkeypatch 替换后端，验证 503 映射而非 500 裸崩。
本端点不读 DashboardServer 字段，直接用纯路由层客户端。
"""

from __future__ import annotations

import base64
from typing import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.modules.dashboard.api.router import create_app
from src.modules.vision.look_at_screen import ScreenCaptureResult
from src.modules.vision.mss_capture import MssScreenCapture


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def _patch_capture(monkeypatch: pytest.MonkeyPatch, *, monitors: bool = True, **capture_behavior) -> MagicMock:
    cap = MagicMock(spec=MssScreenCapture)
    cap.list_monitors.return_value = (
        [MagicMock(index=1, left=0, top=0, width=1920, height=1080, is_primary=True)] if monitors else []
    )
    for attr, value in capture_behavior.items():
        setattr(cap.capture, attr, value)
    monkeypatch.setattr("src.modules.dashboard.api.vision.MssScreenCapture", lambda: cap)
    return cap


def test_monitors_lists_real_displays(client: TestClient) -> None:
    expected = len(MssScreenCapture().list_monitors())
    body = client.get("/api/v1/vision/monitors").json()
    assert set(body) == {"count", "monitors"} and body["count"] == len(body["monitors"]) == expected
    if expected:
        first = body["monitors"][0]
        assert set(first) == {"index", "left", "top", "width", "height", "is_primary"}
        assert first["width"] > 0 and first["height"] > 0 and isinstance(first["is_primary"], bool)


def test_monitors_empty_when_backend_down(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_capture(monkeypatch, monitors=False)
    resp = client.get("/api/v1/vision/monitors")
    assert (resp.status_code, resp.json()) == (200, {"count": 0, "monitors": []})


def test_preview_defaults_and_valid_png(client: TestClient) -> None:
    body = client.get("/api/v1/vision/preview").json()
    assert set(body) == {"image_b64", "width", "height", "monitor_index", "region"}
    assert body["width"] > 0 and body["height"] > 0 and (body["monitor_index"], body["region"]) == (1, None)
    assert base64.b64decode(body["image_b64"])[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize(
    ("params", "monitor_index", "region"),
    [
        ({"region": "0,0,200,200"}, 1, [0, 0, 200, 200]),
        ({"monitor_index": 2, "region": "10,10,110,110"}, 2, [10, 10, 110, 110]),
        ({"region": "200,200,0,0"}, 1, [0, 0, 200, 200]),  # 前端拖框可能反向，自动交换
    ],
)
def test_preview_echoes_monitor_and_normalized_region(client: TestClient, params, monitor_index, region) -> None:
    body = client.get("/api/v1/vision/preview", params=params).json()
    assert (body["monitor_index"], body["region"]) == (monitor_index, region) and body["image_b64"]


def test_preview_max_width_shrinks_image(client: TestClient) -> None:
    assert client.get("/api/v1/vision/preview", params={"max_width": 200}).json()["width"] <= 200


@pytest.mark.parametrize(("region", "hint"), [("a,b,c,d", "整数"), ("1,2,3", "4")])
def test_preview_invalid_region_returns_400(client: TestClient, region: str, hint: str) -> None:
    resp = client.get("/api/v1/vision/preview", params={"region": region})
    detail = resp.json().get("detail", "")
    assert resp.status_code == 400 and (hint in detail or "region" in detail.lower())


@pytest.mark.parametrize(
    ("monitors", "behavior", "hint"),
    [
        (True, {"side_effect": RuntimeError("mss simulated crash")}, "抓取"),
        (True, {"return_value": ScreenCaptureResult(captured_at_ms=0)}, ""),  # image=None
        (False, {}, "显示器"),
    ],
    ids=["capture_raises", "empty_image", "no_monitors"],
)
def test_preview_backend_failures_return_503_not_500(client, monkeypatch, monitors, behavior, hint) -> None:
    _patch_capture(monkeypatch, monitors=monitors, **behavior)
    resp = client.get("/api/v1/vision/preview")
    assert resp.status_code == 503
    detail = str(resp.json().get("detail", ""))
    assert hint in detail or "mss" in detail.lower()
