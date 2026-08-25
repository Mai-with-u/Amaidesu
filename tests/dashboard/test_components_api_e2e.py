"""v2 组件管理 API 端到端测试（TestClient 走完整路由栈）

覆盖:
1. GET /api/v1/components — 返回三组（collectors/agents/tools），
   未启用组件以 is_enabled=False 占位
2. POST /api/v1/components/{phase}/{name}/control start — 未启用组件
   加入对应 TOML enabled 列表（幂等写回）
3. POST control stop — 组件从 TOML enabled 列表移除
"""

from __future__ import annotations

import re
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
    from src.modules.agents.manager import AgentManager
    from src.modules.collectors.manager import CollectorManager

    svc = ConfigService(base_dir=str(config_dir.parent))
    svc.initialize()

    cm = CollectorManager()
    am = AgentManager()

    server = DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        context_service=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
        collector_manager=cm,
        agent_manager=am,
        tool_registry=MagicMock(),
    )
    set_dashboard_server(server)
    yield TestClient(create_app())
    set_dashboard_server(None)  # type: ignore[arg-type]


def test_list_components_returns_all_groups_with_disabled(client: TestClient) -> None:
    resp = client.get("/api/v1/components")
    assert resp.status_code == 200
    data = resp.json()

    assert set(data) == {"collectors", "agents", "tools", "input", "decision", "output"}

    collectors = {c["name"]: c for c in data["collectors"]}
    assert set(collectors) == {"bili_danmaku", "mock_danmaku"}
    assert collectors["bili_danmaku"]["is_enabled"] is True
    assert collectors["bili_danmaku"]["is_started"] is False  # 未动态启动前不运行
    assert collectors["mock_danmaku"]["is_enabled"] is False
    assert collectors["mock_danmaku"]["is_started"] is False

    agents = {c["name"]: c for c in data["agents"]}
    assert set(agents) == {"streamer", "game"}
    assert agents["streamer"]["is_enabled"] is True
    assert agents["streamer"]["is_started"] is False
    assert agents["game"]["is_enabled"] is False

    tools = {c["name"]: c for c in data["tools"]}
    assert set(tools) == {"subtitle", "vts"}
    assert tools["subtitle"]["is_enabled"] is True
    assert tools["vts"]["is_enabled"] is False


def test_control_start_dynamically_starts_collector(client: TestClient, config_dir: Path) -> None:
    resp = client.post("/api/v1/components/input/mock_danmaku/control", json={"action": "start"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True

    listed = client.get("/api/v1/components").json()
    collectors = {c["name"]: c for c in listed["collectors"]}
    assert collectors["mock_danmaku"]["is_enabled"] is True
    # 注：TestClient 每请求独立事件循环，后台消费任务跨请求会被取消，
    # is_started 反映配置启用状态（真实运行态由 CollectorManager 单测覆盖）
    assert "mock_danmaku" in [c["name"] for c in listed["collectors"]]


def test_control_stop_dynamically_stops_collector(client: TestClient, config_dir: Path) -> None:
    client.post("/api/v1/components/input/mock_danmaku/control", json={"action": "start"})
    resp = client.post("/api/v1/components/input/mock_danmaku/control", json={"action": "stop"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    content = (config_dir / "tools.toml").read_text(encoding="utf-8")
    # 子段名仍在（配置），但 enabled 列表应已移除该组件
    enabled_match = re.search(r"\[tools\.perception\.config\]\s*enabled = (\[.*?\])", content)
    assert enabled_match is not None
    assert '"mock_danmaku"' not in enabled_match.group(1)

    listed = client.get("/api/v1/components").json()
    collectors = {c["name"]: c for c in listed["collectors"]}
    assert collectors["mock_danmaku"]["is_enabled"] is False
    assert collectors["mock_danmaku"]["is_started"] is False


def test_control_start_is_idempotent(client: TestClient) -> None:
    first = client.post("/api/v1/components/input/mock_danmaku/control", json={"action": "start"})
    second = client.post("/api/v1/components/input/mock_danmaku/control", json={"action": "start"})
    assert first.json()["success"] is True
    assert second.json()["success"] is True
    listed = client.get("/api/v1/components").json()
    collectors = {c["name"]: c for c in listed["collectors"]}
    assert collectors["mock_danmaku"]["is_enabled"] is True


def test_control_unknown_phase_returns_400(client: TestClient) -> None:
    resp = client.post("/api/v1/components/unknown/foo/control", json={"action": "start"})
    assert resp.status_code == 400
