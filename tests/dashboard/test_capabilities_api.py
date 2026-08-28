"""Capabilities API 测试套件（Wave U1 / B3-B4）

覆盖：
1. **GET /api/v1/capabilities** — 从 ToolRegistry 读取工具清单并转换为
   前端 ParameterSpec 形状
2. **/api/v1/handlers 端点已删除**
3. tool_registry=None → 503
4. parameters_schema 类型映射 + required 列表
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


_CORE_TOML = """\
[meta]
version = "2.0.3"
"""


def _make_spec(name: str, description: str, parameters_schema, provider: str = "builtin"):
    """构造 ToolSpec（避免 import 内部 dataclass 模块路径耦合）。"""
    from typing import cast

    from src.modules.tools.models import Provider, ToolSpec

    return ToolSpec(
        name=name,
        description=description,
        parameters_schema=parameters_schema,
        kind="sync",
        provider=cast(Provider, provider),
    )


class _FakeToolRegistry:
    def __init__(self, specs) -> None:
        self._specs = list(specs)

    def list_tools(self, provider=None):
        return list(self._specs)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "core.toml").write_text(_CORE_TOML, encoding="utf-8")
    return cfg


@pytest.fixture
def client(config_dir: Path):
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.config.service import ConfigService
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    svc = ConfigService(base_dir=str(config_dir.parent))
    svc.initialize()
    server = DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        context_service=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
        tool_registry=_FakeToolRegistry(  # type: ignore[arg-type]
            specs=[
                _make_spec(
                    "speak",
                    "TTS 语音合成",
                    {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "要播报的文本"},
                            "speed": {
                                "type": "number",
                                "default": 1.0,
                                "minimum": 0.5,
                                "maximum": 2.0,
                            },
                            "count": {"type": "integer"},
                        },
                        "required": ["text"],
                    },
                ),
                _make_spec(
                    "show_image",
                    "显示图片",
                    {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "permanent": {"type": "boolean", "default": False},
                        },
                        "required": ["url"],
                    },
                    provider="game",
                ),
            ]
        ),
    )
    set_dashboard_server(server)
    yield TestClient(create_app())
    set_dashboard_server(None)  # type: ignore[arg-type]


def test_capabilities_returns_actions_with_provider_prefix(client: TestClient) -> None:
    """动作名 = provider.tool_name 全限定名。"""
    resp = client.get("/api/v1/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    names = {a["name"] for a in body["actions"]}
    assert "builtin.speak" in names
    assert "game.show_image" in names


def test_capabilities_action_description_passthrough(client: TestClient) -> None:
    resp = client.get("/api/v1/capabilities")
    by_name = {a["name"]: a for a in resp.json()["actions"]}
    assert by_name["builtin.speak"]["description"] == "TTS 语音合成"
    assert by_name["game.show_image"]["description"] == "显示图片"


def test_capabilities_parameters_map_to_parameter_spec(client: TestClient) -> None:
    """parameters_schema.properties[*] → Record<key, ParameterSpec>。"""
    resp = client.get("/api/v1/capabilities")
    by_name = {a["name"]: a for a in resp.json()["actions"]}

    speak_params = by_name["builtin.speak"]["parameters"]
    assert speak_params["text"]["type"] == "string"
    assert speak_params["text"]["required"] is True
    assert speak_params["text"]["description"] == "要播报的文本"
    assert speak_params["speed"]["type"] == "number"
    assert speak_params["speed"]["default"] == 1.0
    assert speak_params["speed"]["minimum"] == 0.5
    assert speak_params["speed"]["maximum"] == 2.0
    assert speak_params["speed"]["required"] is False
    assert speak_params["count"]["type"] == "integer"
    assert speak_params["count"]["required"] is False

    image_params = by_name["game.show_image"]["parameters"]
    assert image_params["url"]["type"] == "string"
    assert image_params["url"]["required"] is True
    assert image_params["permanent"]["type"] == "boolean"
    assert image_params["permanent"]["default"] is False


def test_capabilities_handlers_endpoint_removed(client: TestClient) -> None:
    """Wave U1 / B4：/api/v1/handlers 端点已删除。"""
    resp = client.get("/api/v1/handlers")
    assert resp.status_code == 404


def test_capabilities_returns_503_when_registry_missing(config_dir: Path) -> None:
    """tool_registry 未注入时返回 503（与原契约一致）。"""
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.config.service import ConfigService
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    svc = ConfigService(base_dir=str(config_dir.parent))
    svc.initialize()
    server = DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        context_service=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
        tool_registry=None,
    )
    set_dashboard_server(server)
    try:
        resp = TestClient(create_app()).get("/api/v1/capabilities")
        assert resp.status_code == 503
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]


def test_capabilities_empty_when_registry_empty(config_dir: Path) -> None:
    """空注册表返回 actions=[]（不抛 500）。"""
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.config.service import ConfigService
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    svc = ConfigService(base_dir=str(config_dir.parent))
    svc.initialize()
    server = DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        context_service=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
        tool_registry=_FakeToolRegistry(specs=[]),  # type: ignore[arg-type]
    )
    set_dashboard_server(server)
    try:
        resp = TestClient(create_app()).get("/api/v1/capabilities")
        assert resp.status_code == 200
        assert resp.json() == {"actions": []}
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]


def test_capabilities_magicmock_registry_returns_actions(config_dir: Path) -> None:
    """MagicMock（test_components_v2_api 风格）作为 registry 也能跑通（无异常路径）。"""
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.config.service import ConfigService
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    svc = ConfigService(base_dir=str(config_dir.parent))
    svc.initialize()

    class _EmptyRegistry:
        def list_tools(self, provider=None):
            return []

    server = DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        context_service=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
        tool_registry=MagicMock(spec=_EmptyRegistry),  # type: ignore[arg-type]
    )
    set_dashboard_server(server)
    try:
        resp = TestClient(create_app()).get("/api/v1/capabilities")
        assert resp.status_code == 200
        assert resp.json()["actions"] == []
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]
