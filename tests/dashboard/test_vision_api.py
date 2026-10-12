"""Vision Dashboard API 测试：显示器枚举与预览端点。

- 多数用例走真实 ``MssScreenCapture``（本机 4 显示器，mss 可用），通过
  ``create_app()`` + ``TestClient`` 走完整 HTTP 层；不依赖 DashboardServer 装配
  （本端点不需要 server 字段）。
- 失败路径用例用 ``monkeypatch`` 替换 ``MssScreenCapture`` 让 ``capture`` 抛
  异常 / ``list_monitors`` 返回空，验证 503 映射而非 500 裸崩。
"""

from __future__ import annotations

import base64
from typing import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.modules.vision.look_at_screen import ScreenCaptureResult
from src.modules.vision.mss_capture import MssScreenCapture


@pytest.fixture
def client() -> Iterator[TestClient]:
    """构造纯路由层 TestClient（不依赖 DashboardServer：本端点不读 server 字段）。"""
    from src.modules.dashboard.api.router import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


def test_monitors_returns_real_list(client: TestClient) -> None:
    """``GET /api/v1/vision/monitors`` → 200 + 数组含 index/width/height。

    真机 mss 可用，应至少返回 1 个物理显示器（虚拟合屏 index=0 也会出现）。
    """
    real_cap = MssScreenCapture()
    expected_count = len(real_cap.list_monitors())

    resp = client.get("/api/v1/vision/monitors")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"count", "monitors"}
    assert body["count"] == expected_count
    assert isinstance(body["monitors"], list)
    assert body["count"] == len(body["monitors"])

    if expected_count > 0:
        first = body["monitors"][0]
        assert set(first) == {"index", "left", "top", "width", "height", "is_primary"}
        assert isinstance(first["index"], int)
        assert isinstance(first["width"], int) and first["width"] > 0
        assert isinstance(first["height"], int) and first["height"] > 0
        assert isinstance(first["is_primary"], bool)


def test_monitors_empty_when_backend_down(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """mss 不可用时 monitors 端点返回空列表 + 200（不抛 5xx）。"""
    fake_cap = MagicMock(spec=MssScreenCapture)
    fake_cap.list_monitors.return_value = []
    monkeypatch.setattr(
        "src.modules.dashboard.api.vision.MssScreenCapture",
        lambda: fake_cap,
    )

    resp = client.get("/api/v1/vision/monitors")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["monitors"] == []


def test_preview_default_params(client: TestClient) -> None:
    """``GET /api/v1/vision/preview`` 无参 → 200 + image_b64 非空 + 回显。"""
    resp = client.get("/api/v1/vision/preview")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"image_b64", "width", "height", "monitor_index", "region"}
    assert isinstance(body["image_b64"], str) and len(body["image_b64"]) > 0
    assert body["width"] > 0 and body["height"] > 0
    assert body["monitor_index"] == 1
    assert body["region"] is None

    # image_b64 可解码为有效 PNG
    decoded = base64.b64decode(body["image_b64"])
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n"


def test_preview_with_region_echo(client: TestClient) -> None:
    """``region=0,0,200,200`` → 200 + region 回显 + 图像含叠加层。"""
    resp = client.get("/api/v1/vision/preview", params={"region": "0,0,200,200"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["region"] == [0, 0, 200, 200]
    assert body["monitor_index"] == 1
    # 图应比无 region 时更大（叠加层不改变尺寸），且 base64 非空
    assert body["image_b64"]
    assert body["width"] > 0 and body["height"] > 0


def test_preview_with_monitor_index_and_region(client: TestClient) -> None:
    """``monitor_index=2&region=10,10,110,110`` → 回显 + 尺寸合理。"""
    resp = client.get(
        "/api/v1/vision/preview",
        params={"monitor_index": 2, "region": "10,10,110,110"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["monitor_index"] == 2
    assert body["region"] == [10, 10, 110, 110]


def test_preview_max_width_shrinks_image(client: TestClient) -> None:
    """``max_width=200`` → 返回图宽 ≤ 200（等比缩放）。"""
    resp = client.get("/api/v1/vision/preview", params={"max_width": 200})
    assert resp.status_code == 200
    body = resp.json()
    assert body["width"] <= 200


def test_preview_region_normalizes_reverse_order(client: TestClient) -> None:
    """反向写法的 region 自动交换（前端拖框可能反向），不视为错误。"""
    resp = client.get("/api/v1/vision/preview", params={"region": "200,200,0,0"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["region"] == [0, 0, 200, 200]


def test_preview_invalid_region_non_integer_returns_400(client: TestClient) -> None:
    """``region=a,b,c,d`` → 400 + 明确错误体（**非** 500）。"""
    resp = client.get("/api/v1/vision/preview", params={"region": "a,b,c,d"})
    assert resp.status_code == 400
    body = resp.json()
    detail = body.get("detail", "")
    assert "region" in detail.lower() or "整数" in detail or "integer" in detail.lower()


def test_preview_invalid_region_wrong_arity_returns_400(client: TestClient) -> None:
    """``region=1,2,3``（3 个值）→ 400 + 明确错误体。"""
    resp = client.get("/api/v1/vision/preview", params={"region": "1,2,3"})
    assert resp.status_code == 400
    body = resp.json()
    detail = body.get("detail", "")
    assert "4" in detail  # 明确提示"必须是 4 个"


def test_preview_capture_failure_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """``capture`` 抛异常时 → 503（**非** 500），错误体含失败原因。"""
    fake_cap = MagicMock(spec=MssScreenCapture)
    fake_cap.list_monitors.return_value = [
        # 至少一个物理显示器，否则先在 list_monitors 处 503
        MagicMock(index=1, left=0, top=0, width=1920, height=1080, is_primary=True)
    ]
    fake_cap.capture.side_effect = RuntimeError("mss simulated crash")
    monkeypatch.setattr(
        "src.modules.dashboard.api.vision.MssScreenCapture",
        lambda: fake_cap,
    )

    resp = client.get("/api/v1/vision/preview")
    assert resp.status_code == 503
    body = resp.json()
    detail = str(body.get("detail", ""))
    assert "抓取" in detail or "mss" in detail.lower()


def test_preview_capture_empty_image_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """``capture`` 返回 ``image=None``（mss 后端异常）→ 503。"""
    fake_cap = MagicMock(spec=MssScreenCapture)
    fake_cap.list_monitors.return_value = [MagicMock(index=1, left=0, top=0, width=1920, height=1080, is_primary=True)]
    fake_cap.capture.return_value = ScreenCaptureResult(captured_at_ms=0)
    monkeypatch.setattr(
        "src.modules.dashboard.api.vision.MssScreenCapture",
        lambda: fake_cap,
    )

    resp = client.get("/api/v1/vision/preview")
    assert resp.status_code == 503


def test_preview_no_monitors_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """无显示器（mss 不可用）→ 503，错误体说明"显示器不可用"。"""
    fake_cap = MagicMock(spec=MssScreenCapture)
    fake_cap.list_monitors.return_value = []
    monkeypatch.setattr(
        "src.modules.dashboard.api.vision.MssScreenCapture",
        lambda: fake_cap,
    )

    resp = client.get("/api/v1/vision/preview")
    assert resp.status_code == 503
    detail = str(resp.json().get("detail", ""))
    assert "显示器" in detail or "mss" in detail.lower()


def test_monitors_endpoint_in_router(client: TestClient) -> None:
    """路由已在 create_app() 注册（冒烟：不被 404）。"""
    resp = client.get("/api/v1/vision/monitors")
    assert resp.status_code != 404

    resp2 = client.get("/api/v1/vision/preview")
    assert resp2.status_code != 404
