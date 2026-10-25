"""Dashboard 组合根装配：main.create_app_components 把各仓储原样交给 DashboardServer。

Dashboard 在其他组合根测试里是关着的，参数传错（例如给 ``_start_dashboard`` 多传一个
它不认识的关键字）只会在真实启动时才炸。这里开启 Dashboard、用替身服务器接住构造参数；
数据库指向临时文件，不碰项目里的 ``data/amaidesu.db``；诊断落盘关闭，不往项目 ``logs/``
写事件记录；字幕关闭，不弹真实 Tk 字幕窗口。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

import main as app_main
from src.modules.config.service import ConfigService

# create_app_components 返回元组的下标
EVENT_BUS, LLM, AGENT_MANAGER, DATABASE, LEDGER, SESSION_MANAGER, HEALTH, TASKS = 0, 1, 5, 7, 8, 9, 11, 12


class _FakeDashboardServer:
    """记录构造参数的替身：不监听端口，只提供组合根启动后会碰到的属性与方法。"""

    instances: List["_FakeDashboardServer"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.widget_service = None
        self.ws_handler = None
        _FakeDashboardServer.instances.append(self)

    async def start(self) -> None:
        return None

    def get_url(self) -> str:
        return "http://127.0.0.1:0"


@pytest.fixture(autouse=True)
def _stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    async def noop(self: Any, *args: Any) -> None:
        return None

    monkeypatch.setattr("src.modules.llm.engine.LLMManager.setup", noop)
    monkeypatch.setattr("src.modules.llm.engine.LLMManager.cleanup", noop)
    monkeypatch.setattr("main._BASE_DIR", str(Path.cwd()))
    monkeypatch.setattr("main.DashboardServer", _FakeDashboardServer)
    _FakeDashboardServer.instances.clear()


def _config(db_path: Path) -> Dict[str, Any]:
    return {
        "general": {"platform_id": "test"},
        "context": {},
        "events": {"history_size": 100},
        "dashboard": {"enabled": True, "auto_open_browser": False},
        "logging": {"level": "WARNING"},
        "subtitle": {"enabled": False},
        "diagnostics": {"enabled": False},
        "interceptors": {},
        "simulator": {"enabled": False},
        "tools": {},
        "agents": {"enabled": []},  # 非空：组合根才装配任务跟踪器（Dashboard 依赖它）
        "memory": {},
        "storage": {},
        "sqlite": {"db_path": str(db_path)},
        "background": {},
        "llm_providers": [],
        "llm": {"client_type": "", "model": ""},
        "llm_fast": {"client_type": "", "model": ""},
    }


async def test_dashboard_receives_viewer_repos(tmp_path: Path) -> None:
    config_service = ConfigService(base_dir=str(tmp_path))
    config_service._main_config = _config(tmp_path / "wiring.db")  # 绕开真实 TOML 加载
    config_service._initialized = True
    result = await app_main.create_app_components(
        config=config_service.main_config,
        config_service=config_service,
        dev_webui=False,
        simulator_auto_start=False,
        log_streamer_persist=False,
    )
    try:
        (server,) = _FakeDashboardServer.instances
        for name in ("viewer_repo", "chat_repo", "viewer_profile_repo", "viewer_identity_repo"):
            assert server.kwargs.get(name) is not None, f"DashboardServer 没收到 {name}"
    finally:
        # 日志流在全局日志上挂了 sink，替身服务器不会去停它：这里摘掉，免得污染后续的日志测试
        for server in _FakeDashboardServer.instances:
            if server.kwargs.get("log_streamer") is not None:
                await server.kwargs["log_streamer"].stop()
        # 组合根启动的后台循环逐个停掉，避免测试结束后还在写日志
        for index in (TASKS, HEALTH, SESSION_MANAGER, LEDGER):
            if result[index] is not None:
                await result[index].stop()
        if result[AGENT_MANAGER] is not None:
            await result[AGENT_MANAGER].stop_supervisor()
            await result[AGENT_MANAGER].stop_all()
        await result[EVENT_BUS].cleanup()
        if result[LLM] is not None:
            await result[LLM].cleanup()
        await result[DATABASE].close()
