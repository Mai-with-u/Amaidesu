"""Avatar Debug API：平台调试状态聚合 / 表情与参数触发转发"""

from __future__ import annotations

from typing import Any, List

import pytest
from fastapi.testclient import TestClient

from src.modules.dashboard.api.avatar import DEBUG_PLATFORMS


class _FakeResult:
    """ToolExecutionResult 形态的轻量替身（契约方法返回值）。"""

    def __init__(self, success: bool, error_message: str = "", structured: Any = None) -> None:
        self.success = success
        self.error_message = error_message
        self.structured_content = structured


class _FakeRegistry:
    """只提供已登记 Provider 快照的最小注册表替身（与真注册表一样，0 工具的 Provider 也在册）。"""

    def __init__(self, providers: List[Any]) -> None:
        self._providers = providers

    def providers(self) -> List[Any]:
        return list(self._providers)


class _ConnectedProvider:
    """已连接平台 Provider 替身：契约方法 + 观测面 connection_summary。"""

    PROVIDER_NAME = "vts"

    def __init__(self) -> None:
        self.calls: List[tuple[str, tuple[Any, ...]]] = []

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def connection_summary(self) -> dict[str, Any]:
        return {"is_connected": True, "stats": {"render_count": 3}}

    async def set_expression(self, emotion: str, intensity: float) -> _FakeResult:
        self.calls.append(("set_expression", (emotion, intensity)))
        if emotion == "bad_emotion":
            return _FakeResult(False, error_message=f"未知情绪 '{emotion}'", structured={"emotion": emotion})
        return _FakeResult(True, structured={"emotion": emotion, "intensity": intensity})

    async def list_preset_actions(self) -> _FakeResult:
        self.calls.append(("list_preset_actions", ()))
        return _FakeResult(True, structured={"actions": [{"name": "wave", "type": "hotkey"}]})

    async def trigger_preset_action(self, action: str) -> _FakeResult:
        self.calls.append(("trigger_preset_action", (action,)))
        return _FakeResult(True, structured={"action": action})

    async def set_parameter_value(self, parameter_name: str, value: float) -> bool:
        self.calls.append(("set_parameter_value", (parameter_name, value)))
        return True


class _DisconnectedProvider(_ConnectedProvider):
    """未连接平台 Provider 替身（连接态 False）。"""

    PROVIDER_NAME = "vrchat"

    def connection_summary(self) -> dict[str, Any]:
        return {"is_connected": False, "stats": {}}


class _SheetProvider:
    """表情差分 Provider 替身：就绪态走 connection_summary（无外部连接）。"""

    PROVIDER_NAME = "expression_sheet"

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def connection_summary(self) -> dict[str, Any]:
        return {"is_connected": True, "stats": {}}

    async def set_expression(self, emotion: str, intensity: float) -> _FakeResult:
        self.calls = [("set_expression", (emotion, intensity))]
        return _FakeResult(True, structured={"emotion": emotion, "image_url": "/img/neutral.png"})

    async def list_preset_actions(self) -> _FakeResult:
        return _FakeResult(True, structured={"actions": []})

    async def trigger_preset_action(self, action: str) -> _FakeResult:
        return _FakeResult(False, error_message=f"未知预设动作 '{action}'", structured={"available_actions": []})


@pytest.fixture
def client(mount) -> TestClient:
    """挂载带平台 Provider 替身注册表的 TestClient（未装配平台留空）。"""
    # VTS 不向 LLM 暴露工具、未连接的平台登记 0 工具：调试台都要按名字找得到
    registry = _FakeRegistry([_ConnectedProvider(), _DisconnectedProvider(), _SheetProvider()])
    return mount(tool_registry=registry)


def test_status_lists_all_platforms(client: TestClient) -> None:
    """状态聚合覆盖四平台；已连接 / 未连接 / 未装配各归其位。"""
    resp = client.get("/api/v1/avatar/debug/status")
    assert resp.status_code == 200
    platforms = {p["platform"]: p for p in resp.json()["platforms"]}
    assert set(platforms) == set(DEBUG_PLATFORMS)

    vts = platforms["vts"]
    assert vts["connected"] is True
    assert vts["current_expression"] is None  # 各平台均未暴露当前表情，诚实 null
    assert [a["name"] for a in vts["actions"]] == ["wave"]
    assert len(vts["emotions"]) == 17
    assert vts["stats"]["render_count"] == 3

    vrchat = platforms["vrchat"]
    assert vrchat["connected"] is False

    sheet = platforms["expression_sheet"]
    assert sheet["connected"] is True  # 就绪态经 connection_summary 契约
    assert sheet["actions"] == []

    warudo = platforms["warudo"]
    assert warudo["connected"] is False
    assert "未装配" in warudo["detail"]


def test_trigger_expression_success(client: TestClient) -> None:
    """表情触发经契约方法转发，成功回传结构化载荷。"""
    resp = client.post(
        "/api/v1/avatar/debug/trigger",
        json={"platform": "vts", "kind": "expression", "key": "happy", "value": 0.8},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["structured_content"] == {"emotion": "happy", "intensity": 0.8}


def test_trigger_expression_business_reject_is_200_with_success_false(client: TestClient) -> None:
    """业务拒绝（未知情绪名等）不吞不炸：200 + success=false + 原因。"""
    resp = client.post(
        "/api/v1/avatar/debug/trigger",
        json={"platform": "vts", "kind": "expression", "key": "bad_emotion", "value": 0.5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "bad_emotion" in body["detail"]


def test_trigger_unknown_platform_404(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/avatar/debug/trigger",
        json={"platform": "niconico", "kind": "expression", "key": "happy", "value": 0.5},
    )
    assert resp.status_code == 404
    assert "niconico" in resp.json()["detail"]


def test_trigger_unknown_kind_400(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/avatar/debug/trigger",
        json={"platform": "vts", "kind": "teleport", "key": "x", "value": 0.5},
    )
    assert resp.status_code == 400


def test_trigger_not_connected_409(client: TestClient) -> None:
    """未连接平台触发 409，不静默吞也不静默 no-op。"""
    resp = client.post(
        "/api/v1/avatar/debug/trigger",
        json={"platform": "vrchat", "kind": "expression", "key": "happy", "value": 0.5},
    )
    assert resp.status_code == 409
    assert "未连接" in resp.json()["detail"]


def test_trigger_not_assembled_404(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/avatar/debug/trigger",
        json={"platform": "warudo", "kind": "expression", "key": "happy", "value": 0.5},
    )
    assert resp.status_code == 404
    assert "未装配" in resp.json()["detail"]


def test_trigger_parameter_unsupported_platform_400(client: TestClient) -> None:
    """参数直调只对保留参数写入方法的平台开放（当前仅 VTS）。"""
    resp = client.post(
        "/api/v1/avatar/debug/trigger",
        json={"platform": "expression_sheet", "kind": "parameter", "key": "Mouth", "value": 0.5},
    )
    assert resp.status_code == 400
    assert "不支持参数直调" in resp.json()["detail"]


def test_trigger_parameter_success_on_vts(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/avatar/debug/trigger",
        json={"platform": "vts", "kind": "parameter", "key": "MouthSmile", "value": 0.7},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_trigger_action_forwards_preset(client: TestClient) -> None:
    """预设动作触发走契约 trigger_preset_action。"""
    resp = client.post(
        "/api/v1/avatar/debug/trigger",
        json={"platform": "vts", "kind": "action", "key": "wave", "value": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
