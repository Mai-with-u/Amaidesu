"""系统状态 API 测试套件（Wave U1 / B1-B2）

覆盖：
1. **GET /api/v1/system/status** — v2 响应形状（groups + event_bus）
2. **/api/v1/system/stats 端点已删除**（旧桩清理）
3. 工具组计数全部等于 len(tool_registry)（被动契约，无启用/运行区分）
4. EventBus 总吞吐 = sum(emit_count)
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

_AGENTS_TOML = """\
[agents]
enabled = ["streamer"]

[agents.streamer]
planner_llm = "llm_fast"

[agents.game]
full_screen = true
"""

_TOOLS_TOML = """\
[tools]
enabled = ["perception", "output"]

[tools.perception]
enabled = true
provider = "builtin"

[tools.perception.config]
enabled = ["bili_danmaku"]

[tools.perception.config.bili_danmaku]
room_id = 1

[tools.perception.config.mock_danmaku]
send_interval = 1.0

[tools.output]
enabled = true
provider = "builtin"

[tools.output.config]
enabled = ["subtitle"]

[tools.output.config.subtitle]
font_size = 28

[tools.output.config.vts]
vts_host = "localhost"
"""


class _FakeEventStats:
    def __init__(self, emit_count: int) -> None:
        self.emit_count = emit_count


class _FakeEventBus:
    def __init__(self, stats: dict[str, int]) -> None:
        self._stats = {k: _FakeEventStats(v) for k, v in stats.items()}

    def get_all_stats(self) -> dict[str, _FakeEventStats]:
        return dict(self._stats)


class _FakeToolRegistry:
    def __init__(self, n: int) -> None:
        self._n = n

    def __len__(self) -> int:
        return self._n


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "core.toml").write_text(_CORE_TOML, encoding="utf-8")
    (cfg / "agents.toml").write_text(_AGENTS_TOML, encoding="utf-8")
    (cfg / "tools.toml").write_text(_TOOLS_TOML, encoding="utf-8")
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

    event_bus = _FakeEventBus({"room.message.danmaku": 7, "tool.result.speak": 3})
    tool_registry = _FakeToolRegistry(n=2)

    server = DashboardServer(
        event_bus=event_bus,  # type: ignore[arg-type]
        context_service=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
        collector_manager=MagicMock(),
        agent_manager=MagicMock(),
        tool_registry=tool_registry,  # type: ignore[arg-type]
    )
    server._is_running = True
    set_dashboard_server(server)
    yield TestClient(create_app())
    set_dashboard_server(None)  # type: ignore[arg-type]


def test_status_shape_matches_v2_contract(client: TestClient) -> None:
    """v2 响应字段：running/uptime/version/python_version/groups/event_bus"""
    resp = client.get("/api/v1/system/status")
    assert resp.status_code == 200
    data = resp.json()

    assert data["running"] is True
    assert isinstance(data["uptime_seconds"], (int, float))
    assert isinstance(data["version"], str)
    assert isinstance(data["python_version"], str)

    assert set(data["groups"]) == {"collectors", "agents", "tools"}
    for group in data["groups"].values():
        assert set(group) == {"enabled", "started", "total"}
        assert all(isinstance(v, int) for v in group.values())

    assert set(data["event_bus"]) == {"total_events"}
    assert data["event_bus"]["total_events"] == 10


def test_status_does_not_leak_phase_fields(client: TestClient) -> None:
    """v2：PhaseStatus 字段（input_phase/decision_phase/output_phase）已彻底移除"""
    resp = client.get("/api/v1/system/status")
    data = resp.json()
    assert "input_phase" not in data
    assert "decision_phase" not in data
    assert "output_phase" not in data


def test_status_groups_count_correctly(client: TestClient) -> None:
    """采集器/Agent 计数来自 component_helper（enabled = 配置 enabled 列表）。"""
    resp = client.get("/api/v1/system/status").json()

    collectors = resp["groups"]["collectors"]
    assert collectors["total"] == 2  # bili_danmaku + mock_danmaku
    assert collectors["enabled"] == 1  # bili_danmaku 启用

    agents = resp["groups"]["agents"]
    assert agents["total"] == 2  # streamer + game
    assert agents["enabled"] == 1  # streamer

    tools = resp["groups"]["tools"]
    assert tools["total"] == 2  # tool_registry 长度
    assert tools["enabled"] == tools["total"]  # 工具无启用语义，三项相等
    assert tools["started"] == tools["total"]


def test_stats_endpoint_removed(client: TestClient) -> None:
    """Wave U1 / B2：/api/v1/system/stats 端点已删除"""
    resp = client.get("/api/v1/system/stats")
    assert resp.status_code == 404


def test_health_endpoint_still_present(client: TestClient) -> None:
    """健康检查不受 Wave U1 影响。"""
    resp = client.get("/api/v1/system/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_status_event_bus_empty_when_stats_disabled(config_dir: Path) -> None:
    """EventBus 无 get_all_stats() 时 total_events 兜底为 0。"""
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.config.service import ConfigService
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    svc = ConfigService(base_dir=str(config_dir.parent))
    svc.initialize()

    bus = MagicMock()
    bus.get_all_stats = MagicMock(return_value={})

    server = DashboardServer(
        event_bus=bus,
        context_service=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
    )
    set_dashboard_server(server)
    try:
        resp = TestClient(create_app()).get("/api/v1/system/status")
        assert resp.status_code == 200
        assert resp.json()["event_bus"]["total_events"] == 0
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]
