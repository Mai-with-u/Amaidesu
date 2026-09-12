"""DashboardServer 路由解析测试（自描述协议）"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def server(config_dir: Path):
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.config.service import ConfigService
    from src.modules.dashboard.server import DashboardServer

    svc = ConfigService(base_dir=str(config_dir.parent))
    svc.initialize()
    return DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        context_service=None,  # type: ignore[arg-type]
        config_service=svc,
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
    )


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    from src.modules.config.multi_file_loader import generate_default_configs

    cfg = tmp_path / "config"
    cfg.mkdir()
    generate_default_configs(cfg)
    return cfg


@pytest.mark.parametrize(
    ("scope", "file_name"),
    [
        ("agents", "agents.toml"),
        ("collectors", "collectors.toml"),
        ("tools", "tools.toml"),
        ("model", "model.toml"),
        ("storage", "storage.toml"),
        ("infra", "infra.toml"),
    ],
)
def test_get_config_path_resolves_six_scopes(server, config_dir: Path, scope: str, file_name: str):
    """六 scope 全部经自描述协议解析到正确文件"""
    resolved = Path(server.get_config_path(scope))
    assert resolved == config_dir / file_name
    assert resolved.exists()


def test_get_config_path_unknown_scope_returns_none(server):
    """未知 scope 返回 None（不再兜底到已消亡的 core.toml）"""
    assert server.get_config_path("persona") is None
    assert server.get_config_path("content_engine") is None
    assert server.get_config_path(None) is None


def test_get_config_path_without_service_returns_none():
    from src.modules.dashboard.server import DashboardServer

    bare = DashboardServer.__new__(DashboardServer)
    bare.config_service = None
    assert bare.get_config_path("agents") is None
