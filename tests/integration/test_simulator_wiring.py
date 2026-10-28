"""SimulatorService 组合根装配：main.create_app_components 的装配契约。

① [simulator].enabled=false → 零装配（simulator_service 字段为 None）
② enabled=true + auto_start=True → 装配且 is_running=True
③ enabled=true + auto_start=False（--dry 场景）→ 装配但 is_running=False，run_shutdown 照常关闭

组合根内部依赖 EventBus/LLMManager/ConfigService 等组件：用最小 config 让 CollectorManager/AgentManager/
DashboardServer 走零装配路径，并 monkey-patch LLMManager.setup 跳过真实 provider 池校验（测试目标不是 LLM）。
数据库指向临时文件（不碰项目里的 ``data/amaidesu.db``），诊断落盘关闭（不往项目 ``logs/``
写事件记录），字幕关闭（不弹真实 Tk 字幕窗口，它的后台线程会在用例结束后继续写日志，
混进后面日志测试的输出）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

import main as app_main
from src.modules.agents.manager import AgentManager
from src.modules.config.service import ConfigService
from src.modules.logging.log_streamer import _LIVE_STREAMERS
from src.modules.simulator.service import SimulatorService


def _minimal_config(simulator_enabled: bool, db_path: Path) -> Dict[str, Any]:
    """只填 create_app_components 必经路径所需的最小字段，不含真实生产配置。"""
    return {
        "general": {"platform_id": "test"},
        "context": {},
        "events": {"history_size": 100},
        "dashboard": {"enabled": False},
        "logging": {"level": "WARNING"},
        "subtitle": {"enabled": False},
        "diagnostics": {"enabled": False},
        "interceptors": {},
        "simulator": {
            "enabled": simulator_enabled,
            "base_rate_per_minute": 60.0,
            "cadence_mode": "fixed",
            "fixed_interval_s": 1.0,
        },
        "tools": {},
        "agents": {},
        "memory": {},
        "storage": {},
        "sqlite": {"db_path": str(db_path)},
        "background": {},
        "llm_providers": [],  # 空 provider 池：LLMManager.setup 被替身掉，不报错即可
        "llm": {"client_type": "", "model": ""},
        "llm_fast": {"client_type": "", "model": ""},
    }


@pytest.fixture(autouse=True)
def _stub_llm_and_base_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """绕开 LLMManager 的真实 provider 校验与 Dashboard 装配的基座目录依赖。"""

    async def noop(self: Any, *args: Any) -> None:
        return None

    monkeypatch.setattr("src.modules.llm.engine.LLMManager.setup", noop)
    monkeypatch.setattr("src.modules.llm.engine.LLMManager.cleanup", noop)
    monkeypatch.setattr("main._BASE_DIR", str(Path.cwd()))


async def _assemble(tmp_path: Path, *, simulator_enabled: bool, auto_start: bool = True, **config_patch: Any):
    config_service = ConfigService(base_dir=str(tmp_path))
    # 绕开真实 TOML 加载；库文件放进本用例的临时目录
    config_service._main_config = _minimal_config(simulator_enabled, tmp_path / "wiring.db") | config_patch
    config_service._initialized = True
    return await app_main.create_app_components(
        config=config_service.main_config,
        config_service=config_service,
        dev_webui=False,
        simulator_auto_start=auto_start,
        log_streamer_persist=False,
    )


async def _cleanup(result: app_main.AppComponents) -> None:
    if result.agent_manager is not None:
        await result.agent_manager.stop_supervisor()
        await result.agent_manager.stop_all()
    if result.simulator_service is not None:
        await result.simulator_service.cleanup()
    await result.event_bus.cleanup()
    if result.llm_service is not None:
        await result.llm_service.cleanup()
    # 关掉临时库的连接，临时目录才能被清理
    await result.database.close()


async def test_log_streamer_persist_dir_is_passed_through_as_is(tmp_path: Path) -> None:
    """绝对路径原样作为落盘目录（不落入真实 data/logs/）。"""
    streamer = await app_main._start_log_streamer(persist=True, persist_dir=str(tmp_path))
    try:
        assert streamer.persist is True and streamer._persist_dir == tmp_path.resolve()
    finally:
        await streamer.stop()


async def test_disabled_simulator_is_not_wired_at_all(tmp_path: Path) -> None:
    result = await _assemble(tmp_path, simulator_enabled=False)
    try:
        assert result.simulator_service is None
        assert result.session_manager is not None  # 会话管理器始终装配
        assert result.maibot_sync_reporter is None  # 未配置 [maibot_sync] 时不同步直播近况
    finally:
        await _cleanup(result)
    assert not [s for s in _LIVE_STREAMERS if s.persist]  # 测试装配不得留下 persist LogStreamer（会写真实 data/logs/）


@pytest.mark.parametrize("auto_start", [True, False], ids=["auto_start", "dry_mode"])
async def test_enabled_simulator_runs_only_when_auto_started(tmp_path: Path, auto_start: bool) -> None:
    """--dry 模式装配但不启动主循环，因而不产生 LLM 调用。"""
    result = await _assemble(tmp_path, simulator_enabled=True, auto_start=auto_start)
    try:
        assert result.simulator_service is not None
        assert result.simulator_service.is_running is auto_start
    finally:
        await _cleanup(result)
    assert result.simulator_service.is_running is False


async def test_simulator_setup_runs_after_agents_started(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """装配顺序：simulator.setup（回放模式开播会发 live.started）必须晚于 agent_manager.start_all（订阅生效），
    否则开播边界事件被主播 Agent 错过、主动发言闸不置位。"""
    order: list[str] = []
    real_start_all, real_setup = AgentManager.start_all, SimulatorService.setup

    async def spy_start_all(self: AgentManager) -> None:
        await real_start_all(self)
        order.append("agents_started")

    async def spy_setup(self: SimulatorService, *args: Any, **kwargs: Any) -> None:
        order.append("simulator_setup")
        await real_setup(self, *args, **kwargs)

    monkeypatch.setattr(AgentManager, "start_all", spy_start_all)
    monkeypatch.setattr(SimulatorService, "setup", spy_setup)

    # 装配一个最小 streamer Agent，使 start_all 真实执行
    result = await _assemble(tmp_path, simulator_enabled=True, agents={"enabled": ["streamer"], "streamer": {}})
    try:
        assert order == ["agents_started", "simulator_setup"]
    finally:
        await _cleanup(result)


async def test_dry_mode_shutdown_completes_without_starting_the_simulator(tmp_path: Path) -> None:
    result = await _assemble(tmp_path, simulator_enabled=True, auto_start=False)
    assert result.simulator_service is not None
    assert result.simulator_service.is_running is False

    await app_main.run_shutdown(result)  # 不抛错即关闭正常
