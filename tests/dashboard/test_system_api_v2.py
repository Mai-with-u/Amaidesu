"""系统状态 API：/system/status 的 v2 形状与计数口径、健康检查、更新日志降级，旧 /stats 端点已下线"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import src.modules.dashboard.api.system as system_api
from src.modules.agents.companions import COMPANION_COLLECTORS
from src.modules.config.multi_file_loader import generate_default_configs
from src.modules.config.registry import COLLECTOR_SCHEMAS, ensure_component_registry
from src.modules.config.service import ConfigService


class _FakeEventBus:
    def __init__(self, emit_counts: dict[str, int]) -> None:
        self._stats = {name: MagicMock(emit_count=count) for name, count in emit_counts.items()}

    def get_all_stats(self) -> dict:
        return dict(self._stats)


@pytest.fixture
def config_service(tmp_path: Path) -> ConfigService:
    """七文件基线（组件计数断言基于生成基线）。"""
    cfg = tmp_path / "config"
    cfg.mkdir()
    generate_default_configs(cfg)
    service = ConfigService(base_dir=str(tmp_path))
    service.initialize()
    return service


@pytest.fixture
def client(mount, config_service: ConfigService):
    registry = MagicMock()
    registry.__len__.return_value = 2
    server_kwargs = {
        "event_bus": _FakeEventBus({"room.message.danmaku": 7, "tool.result.speak": 3}),
        "config_service": config_service,
        "collector_manager": MagicMock(),
        "agent_manager": MagicMock(),
        "tool_registry": registry,
    }
    client = mount(**server_kwargs)
    from src.modules.dashboard.dependencies import get_dashboard_server

    get_dashboard_server()._is_running = True
    return client


def test_status_shape_and_event_bus_total(client) -> None:
    data = client.get("/api/v1/system/status").json()
    assert data["running"] is True and isinstance(data["uptime_ms"], int)
    assert isinstance(data["version"], str) and isinstance(data["python_version"], str)
    assert set(data["groups"]) == {"collectors", "agents", "tools"}
    for group in data["groups"].values():
        assert set(group) == {"enabled", "started", "total"} and all(isinstance(v, int) for v in group.values())
    assert data["event_bus"] == {"total_events": 10}  # sum(emit_count)
    assert not {"input_phase", "decision_phase", "output_phase"} & set(data)  # PhaseStatus 字段已彻底移除


def test_status_groups_count_correctly(client) -> None:
    """物化语义下 collectors.toml 含全部在册段（未启用也出卡），total = 注册表采集器全集 + 随 Agent 的附属采集器。"""
    ensure_component_registry()
    groups = client.get("/api/v1/system/status").json()["groups"]

    companions = sum(len(names) for names in COMPANION_COLLECTORS.values())
    assert groups["collectors"]["total"] == len(COLLECTOR_SCHEMAS) + companions
    assert groups["collectors"]["enabled"] == 1  # 基线默认只有 console_input；附属采集器随未启用的 Agent 不算
    assert (groups["agents"]["total"], groups["agents"]["enabled"]) == (4, 1)  # 4 个 Agent 段，仅 streamer 启用
    # 工具无启用语义：三项都等于 len(tool_registry)
    assert groups["tools"] == {"enabled": 2, "started": 2, "total": 2}


def test_event_bus_total_falls_back_to_zero_without_stats(mount, config_service) -> None:
    bus = MagicMock()
    bus.get_all_stats = MagicMock(return_value={})
    body = mount(event_bus=bus, config_service=config_service).get("/api/v1/system/status").json()
    assert body["event_bus"]["total_events"] == 0


def test_removed_stats_endpoint_and_health(client) -> None:
    assert client.get("/api/v1/system/stats").status_code == 404
    resp = client.get("/api/v1/system/health")
    assert (resp.status_code, resp.json()["status"]) == (200, "ok")


def test_changelog_serves_repo_file_and_degrades_to_503(client, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = client.get("/api/v1/system/changelog")
    repo_changelog = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
    assert resp.status_code == 200 and resp.json() == {"content": repo_changelog.read_text(encoding="utf-8")}

    monkeypatch.setattr(system_api, "_CHANGELOG_PATH", Path("<nonexistent>") / "CHANGELOG.md")
    assert client.get("/api/v1/system/changelog").status_code == 503
