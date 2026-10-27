"""系统状态 API：/system/status 的 v2 形状与计数口径、健康检查、更新日志降级，旧 /stats 端点已下线"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import src.modules.dashboard.api.system as system_api
from src.modules.agents.companions import COMPANION_COLLECTORS
from src.modules.config.multi_file_loader import generate_default_configs
from src.modules.component_catalog import ensure_component_registry
from src.modules.config.registry import COLLECTOR_SCHEMAS
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


# 连接健康墙（/system/health 的 domains 聚合）


class _FakeTtsEngine:
    def __init__(self, stats: dict) -> None:
        self._stats = stats

    def get_stats(self) -> dict:
        return dict(self._stats)


class _FakeProvider:
    """模拟工具提供者：category/连接态可调；连接态一律走观测面契约。

    ``contract_connected=False``（缺省 None）时本替身不实现
    ``connection_summary``——覆盖迁移未完成的非契约 Provider（健康墙按
    unknown 降级，不再探测私有成员）。
    """

    def __init__(
        self,
        name: str,
        category: str = "",
        *,
        contract_connected: bool | None = None,
        last_error: str = "",
        supports_reconnect: bool = False,
    ) -> None:
        self.name = name
        self.category = category
        self.last_error = last_error
        self.supports_reconnect = supports_reconnect
        if contract_connected is not None:
            self.connection_summary = lambda: {"is_connected": contract_connected, "stats": {}}


class _FakeRegistry:
    def __init__(self, providers: list) -> None:
        self._providers = providers

    def __len__(self) -> int:
        return len(self._providers)

    def providers(self) -> list:
        return list(self._providers)


def _health_client(mount, config_service: ConfigService, **components):
    registry = MagicMock()
    registry.__len__.return_value = 2
    server_kwargs = {
        "event_bus": _FakeEventBus({}),
        "config_service": config_service,
        "tool_registry": components.pop("tool_registry", registry),
        **components,
    }
    client = mount(**server_kwargs)
    from src.modules.dashboard.dependencies import get_dashboard_server

    get_dashboard_server()._is_running = True
    return client


def test_health_wall_shape_and_unknown_placeholder(mount, config_service: ConfigService) -> None:
    """无 TTS/工具装配时按 unknown 占位如实展示，不硬造状态。"""
    client = _health_client(mount, config_service)
    body = client.get("/api/v1/system/health").json()
    assert body["status"] == "ok" and isinstance(body["checked_at_ms"], int)
    assert set(body["domains"]) == {"tts", "avatar", "obs", "maibot", "tools_providers", "collectors", "agents"}
    assert body["domains"]["tts"][0]["state"] == "unknown"
    assert body["domains"]["maibot"][0]["state"] == "unknown"
    for domain_items in body["domains"].values():
        for item in domain_items:
            assert set(item) == {"name", "state", "detail", "last_error", "supports_reconnect"}
            assert item["state"] in {"ok", "degraded", "offline", "unknown"}


def test_health_wall_tts_and_provider_states(mount, config_service: ConfigService) -> None:
    """TTS 走引擎 get_stats；提供者按分类归域（avatar/obs/其余），连接态走观测面契约。"""
    registry = _FakeRegistry(
        [
            _FakeProvider("vts", "avatar", contract_connected=True, supports_reconnect=True),
            _FakeProvider("warudo", "avatar", contract_connected=False, supports_reconnect=True, last_error="连接超时"),
            _FakeProvider("obs", "studio", contract_connected=True, last_error="上次抖动"),
            _FakeProvider("web_search", "web", contract_connected=True),
            _FakeProvider("mystery", "", supports_reconnect=True),
        ]
    )
    client = _health_client(
        mount,
        config_service,
        tool_registry=registry,
        tts_engine=_FakeTtsEngine({"name": "GPTSoVITS", "is_connected": True}),
    )
    domains = client.get("/api/v1/system/health").json()["domains"]

    assert domains["tts"] == [
        {"name": "tts", "state": "ok", "detail": "引擎 GPTSoVITS", "last_error": "", "supports_reconnect": False}
    ]

    avatar = {item["name"]: item for item in domains["avatar"]}
    assert avatar["vts"]["state"] == "ok" and avatar["vts"]["supports_reconnect"] is True
    assert avatar["warudo"]["state"] == "offline" and avatar["warudo"]["last_error"] == "连接超时"

    assert domains["obs"][0]["name"] == "obs"
    assert domains["obs"][0]["state"] == "degraded"  # 已连接但残留最近失败摘要

    others = {item["name"]: item for item in domains["tools_providers"]}
    assert set(others) == {"web_search", "mystery"}
    assert others["web_search"]["state"] == "ok"
    assert others["mystery"]["state"] == "unknown" and others["mystery"]["supports_reconnect"] is True


def test_health_wall_tts_without_connection_info(mount, config_service: ConfigService) -> None:
    client = _health_client(mount, config_service, tts_engine=_FakeTtsEngine({"name": "X"}))
    assert client.get("/api/v1/system/health").json()["domains"]["tts"][0]["state"] == "unknown"


def test_health_wall_domain_probe_failure_degrades(mount, config_service: ConfigService, monkeypatch) -> None:
    """单域探查异常只降级该域为 unknown 项，不影响其余域与整体 200。"""
    client = _health_client(mount, config_service, tts_engine=object())
    monkeypatch.setattr(system_api, "_build_component_items", lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
    body = client.get("/api/v1/system/health").json()
    assert body["domains"]["collectors"] == [
        {
            "name": "collectors",
            "state": "unknown",
            "detail": "状态探查失败",
            "last_error": "",
            "supports_reconnect": False,
        }
    ]
    assert body["domains"]["tts"][0]["state"] == "unknown"


def test_changelog_serves_repo_file_and_degrades_to_503(client, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = client.get("/api/v1/system/changelog")
    repo_changelog = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
    assert resp.status_code == 200 and resp.json() == {"content": repo_changelog.read_text(encoding="utf-8")}

    monkeypatch.setattr(system_api, "_CHANGELOG_PATH", Path("<nonexistent>") / "CHANGELOG.md")
    assert client.get("/api/v1/system/changelog").status_code == 503
