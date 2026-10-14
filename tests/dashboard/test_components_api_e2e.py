"""v2 组件管理 API 端到端测试（TestClient 走完整路由栈）

覆盖:
1. GET /api/v1/components — 返回两组（collectors/agents）+ 旧阶段兼容字段，
   未启用组件以 is_enabled=False 占位
2. POST /api/v1/components/{group}/{name}/control start — 未启用组件经统一
   写回器 update_config_values 加入 enabled 名单（collectors.toml 顶层 /
   agents.toml [agents]），幂等
3. POST control stop — 组件从 enabled 名单移除

新契约：collectors.toml 的 enabled 名单与各采集器段在文件顶层（无
[collectors] 嵌套段）；落盘断言一律走 tomllib 只读。
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


def _load_toml(config_dir: Path, file_name: str) -> dict:
    """读测试 config 文件为 dict（tomllib 只读；配置管线落盘带 BOM，utf-8-sig 剥除）。"""
    text = (config_dir / file_name).read_text(encoding="utf-8-sig")
    return tomllib.loads(text)


@pytest.fixture
def client(tmp_path: Path):
    from src.modules.agents.manager import AgentManager
    from src.modules.collectors.manager import CollectorManager
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.config.multi_file_loader import update_config_values
    from src.modules.config.service import ConfigService
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # 首启生成七文件基线，再经统一写回器铺出测试态（ collectors 段需过注册表校验）
    svc = ConfigService(base_dir=str(tmp_path))
    svc.initialize()
    update_config_values(
        config_dir,
        "collectors.toml",
        {
            "enabled": ["bili_danmaku"],
            "bili_danmaku": {"room_id": 1},
            "console_input": {},  # 空配置段：未启用占位（Schema 全默认值）
        },
    )
    update_config_values(config_dir, "agents.toml", {"agents.enabled": ["streamer"]})

    cm = CollectorManager()
    am = AgentManager()

    server = DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
        collector_manager=cm,
        agent_manager=am,
        tool_registry=MagicMock(),
    )
    set_dashboard_server(server)
    yield TestClient(create_app()), config_dir
    set_dashboard_server(None)  # type: ignore[arg-type]


def _collectors(http_client) -> dict:
    return {c["name"]: c for c in http_client.get("/api/v1/components").json()["collectors"]}


def test_list_components_returns_all_groups_with_disabled(client) -> None:
    http_client, _config_dir = client
    resp = http_client.get("/api/v1/components")
    assert resp.status_code == 200
    data = resp.json()

    # 工具不在组件清单（域开关单元归 tools API 的 categories 端点管）
    assert set(data) == {"collectors", "agents"}

    collectors = {c["name"]: c for c in data["collectors"]}
    # 组件契约全集 = collectors.toml 物化段全集（未启用也出卡，可先配后启）∪ 各 Agent 的附属采集器；
    # enabled 名单看 is_enabled。conftest 手写的 bili/console_input 两段
    # 之外，物化补齐的 stt / bili_official 同样在列；附属采集器按所属 Agent 是否启用列出并标 owner。
    from src.modules.agents.companions import COMPANION_COLLECTORS
    from src.modules.config.registry import COLLECTOR_SCHEMAS, ensure_component_registry

    ensure_component_registry()
    companions = {name for names in COMPANION_COLLECTORS.values() for name in names}
    assert set(collectors) == set(COLLECTOR_SCHEMAS) | companions
    assert collectors["maicraft_attention"]["owner"] == "minecraft"
    assert collectors["maicraft_attention"]["is_enabled"] is False  # minecraft 未启用
    assert collectors["console_input"]["owner"] is None
    assert collectors["bili_danmaku"]["is_enabled"] is True
    assert collectors["bili_danmaku"]["is_started"] is False  # 未动态启动前均如此
    assert collectors["console_input"]["is_enabled"] is False
    assert collectors["console_input"]["is_started"] is False
    assert collectors["stt"]["is_enabled"] is False  # 物化补的段不自动启用

    agents = {c["name"]: c for c in data["agents"]}
    assert set(agents) == {"streamer", "minecraft", "text_adv", "game_2048"}
    assert agents["streamer"]["is_enabled"] is True
    assert agents["streamer"]["is_started"] is False
    assert agents["minecraft"]["is_enabled"] is False
    assert agents["text_adv"]["is_enabled"] is False
    assert agents["game_2048"]["is_enabled"] is False


def test_companion_collector_cannot_be_toggled_alone(client) -> None:
    """附属采集器随所属 Agent 开关：单独开关被拒，提示去开关 Agent，也不写进 collectors.toml。"""
    http_client, config_dir = client
    resp = http_client.post("/api/v1/components/collectors/maicraft_attention/control", json={"action": "start"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "随 Agent minecraft" in body["message"]
    assert "maicraft_attention" not in (config_dir / "collectors.toml").read_text(encoding="utf-8-sig")


def test_control_start_stop_are_idempotent_and_move_enabled_list(client) -> None:
    http_client, config_dir = client
    control = "/api/v1/components/collectors/console_input/control"
    for _ in range(2):  # 重复 start 幂等
        assert http_client.post(control, json={"action": "start"}).json()["success"] is True
    # TestClient 每请求独立事件循环，后台消费任务跨请求会被取消，is_started 只反映配置启用状态
    assert _collectors(http_client)["console_input"]["is_enabled"] is True

    assert http_client.post(control, json={"action": "stop"}).json()["success"] is True
    # enabled 在 collectors.toml 顶层：名单已移除，子段仍在
    assert "console_input" not in _load_toml(config_dir, "collectors.toml")["enabled"]
    assert "bili_danmaku" in _load_toml(config_dir, "collectors.toml")["enabled"]
    stopped = _collectors(http_client)["console_input"]
    assert (stopped["is_enabled"], stopped["is_started"]) == (False, False)


def test_control_unknown_group_returns_400(client) -> None:
    resp = client[0].post("/api/v1/components/unknown/foo/control", json={"action": "start"})
    assert resp.status_code == 400
