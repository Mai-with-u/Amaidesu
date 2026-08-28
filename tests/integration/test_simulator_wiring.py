"""ADR-006 SimulatorService 组合根装配测试。

测试目标：验证 main.create_app_components 中 SimulatorService 装配契约：

① [simulator].enabled=false → 零装配（simulator_service 元组项为 None）
② [simulator].enabled=true + auto_start=True → 装配且 is_running=True
③ [simulator].enabled=true + auto_start=False（--dry 场景）→ 装配但 is_running=False

组合根内部依赖 EventBus/LLMManager/ConfigService 等组件，本测试用最小 config
让 CollectorManager/AgentManager/DashboardServer 走零装配路径，并通过
monkey-patch LLMManager.setup 跳过真实 LLM provider 池校验（测试目标不是 LLM）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.config.service import ConfigService
from src.modules.events.event_bus import EventBus


def _minimal_config(simulator_enabled: bool = False) -> Dict[str, Any]:
    """最小可装配 config：让 CollectorManager/AgentManager/DashboardServer 走零装配。

    只填充 create_app_components 必经路径所需的最小字段，不含真实生产配置。
    """
    return {
        "general": {"platform_id": "test"},
        "context": {},
        "events": {"history_size": 100, "persist": False},
        "dashboard": {"enabled": False},  # 关闭 dashboard
        "logging": {"level": "WARNING"},
        "interceptors": {},  # 空拦截器
        "simulator": {
            "enabled": simulator_enabled,
            "base_rate_per_minute": 60.0,  # 高频以便测试快速产出
            "cadence_mode": "fixed",
            "fixed_interval_s": 1.0,
        },
        "tools": {},  # 空工具
        "agents": {},  # 空 Agent
        "memory": {},
        "storage": {},
        "background": {},
        # model.toml 顶层段（LLM 装配所需）
        "llm_providers": [],  # 空 provider 池——LLMManager.setup 不报错即可
        "llm": {"client_type": "", "model": ""},
        "llm_fast": {"client_type": "", "model": ""},
    }


@pytest.fixture
def config_service_factory(tmp_path: Path):
    """提供临时 config_service 工厂：tests 隔离、不污染真实 config/ 目录。"""
    created: list[ConfigService] = []

    def _make(simulator_enabled: bool = False) -> ConfigService:
        cs = ConfigService(base_dir=str(tmp_path))
        # 绕开真实 TOML 加载：直接注入私有字段（main_config 是只读 property）
        cs._main_config = _minimal_config(simulator_enabled=simulator_enabled)
        cs._initialized = True
        created.append(cs)
        return cs

    yield _make
    for cs in created:
        # ConfigService 无显式 close 接口（无 I/O 持有）
        pass


class TestSimulatorWiring:
    """SimulatorService 组合根装配契约测试（ADR-006）。"""

    @pytest.fixture(autouse=True)
    def _stub_llm_setup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """绕开 LLMManager.setup 的真实 provider 校验（测试目标非 LLM）。"""
        async def _noop_setup(self, config: Any) -> None:
            return None

        async def _noop_cleanup(self) -> None:
            return None

        monkeypatch.setattr(
            "src.modules.llm.manager.LLMManager.setup", _noop_setup
        )
        monkeypatch.setattr(
            "src.modules.llm.manager.LLMManager.cleanup", _noop_cleanup
        )

    @pytest.mark.asyncio
    async def test_disabled_means_zero_wiring(
        self, config_service_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[simulator].enabled=false → simulator_service 元组项为 None（零装配）。"""
        from main import create_app_components

        # 绕过 Dashboard 装配的外部依赖（基座目录）
        monkeypatch.setattr("main._BASE_DIR", str(Path.cwd()))

        config_service = config_service_factory(simulator_enabled=False)
        config = config_service.main_config

        result = await create_app_components(
            config=config,
            config_service=config_service,
            dev_webui=False,
        )
        # 元组 9 项 → 10 项（v2.0.5 新增 storage_ledger 在尾）：
        # 第 8 项 (index=7) 是 simulator_service；第 10 项 (index=9) 是 storage_ledger
        assert len(result) == 10, f"组合根元组应返回 10 项，实际 {len(result)}"
        simulator_service = result[7]
        assert simulator_service is None, (
            "enabled=false 时 simulator_service 应为 None（零装配）"
        )

        # 清理已装配的资源
        await result[1].cleanup()  # event_bus
        if result[0] is not None:
            await result[0].cleanup()  # context_service
        if result[2] is not None:
            await result[2].cleanup()  # llm_service

    @pytest.mark.asyncio
    async def test_enabled_dry_mode_no_llm_call(
        self, config_service_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """enabled=true + auto_start=False（--dry 模式）→ 装配但 is_running=False（不产生 LLM 调用）。"""
        from main import create_app_components

        monkeypatch.setattr("main._BASE_DIR", str(Path.cwd()))

        config_service = config_service_factory(simulator_enabled=True)
        config = config_service.main_config

        # auto_start=False 模拟 --dry 模式
        result = await create_app_components(
            config=config,
            config_service=config_service,
            dev_webui=False,
            simulator_auto_start=False,
        )
        simulator_service = result[7]
        assert simulator_service is not None, (
            "enabled=true 时 simulator_service 应被装配"
        )
        assert simulator_service.is_running is False, (
            "auto_start=False 时 simulator_service.is_running 应为 False（不启动主循环）"
        )

        # 清理
        await simulator_service.cleanup()
        await result[1].cleanup()
        if result[0] is not None:
            await result[0].cleanup()
        if result[2] is not None:
            await result[2].cleanup()

    @pytest.mark.asyncio
    async def test_enabled_runs_when_auto_started(
        self, config_service_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """enabled=true + auto_start=True → 装配且 is_running=True。"""
        from main import create_app_components

        monkeypatch.setattr("main._BASE_DIR", str(Path.cwd()))

        config_service = config_service_factory(simulator_enabled=True)
        config = config_service.main_config

        result = await create_app_components(
            config=config,
            config_service=config_service,
            dev_webui=False,
            simulator_auto_start=True,
        )
        simulator_service = result[7]
        assert simulator_service is not None
        assert simulator_service.is_running is True, (
            "enabled=true + auto_start=True 时 simulator_service 应已自动启动"
        )

        # 清理：先 stop 主循环再 cleanup
        await simulator_service.cleanup()
        assert simulator_service.is_running is False
        await result[1].cleanup()
        if result[0] is not None:
            await result[0].cleanup()
        if result[2] is not None:
            await result[2].cleanup()


class TestMainDryModeShutdown:
    """main() --dry 路径行为验证（避免 --dry 触发 LLM 调用）。"""

    @pytest.fixture(autouse=True)
    def _stub_llm_setup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _noop_setup(self, config: Any) -> None:
            return None

        async def _noop_cleanup(self) -> None:
            return None

        monkeypatch.setattr(
            "src.modules.llm.manager.LLMManager.setup", _noop_setup
        )
        monkeypatch.setattr(
            "src.modules.llm.manager.LLMManager.cleanup", _noop_cleanup
        )

    @pytest.mark.asyncio
    async def test_dry_mode_does_not_start_simulator(
        self, config_service_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--dry 模式下 enabled=true 也不应启动模拟器主循环（避免 LLM 调用）。"""
        from main import create_app_components, run_shutdown

        monkeypatch.setattr("main._BASE_DIR", str(Path.cwd()))

        config_service = config_service_factory(simulator_enabled=True)
        config = config_service.main_config

        # 模拟 main() --dry 分支：simulator_auto_start=not args.dry
        result = await create_app_components(
            config=config,
            config_service=config_service,
            dev_webui=False,
            simulator_auto_start=False,  # --dry 模式
        )
        simulator_service = result[7]
        assert simulator_service is not None
        assert simulator_service.is_running is False, (
            "--dry 模式下 simulator_service 不应启动主循环"
        )

        # run_shutdown 也应正常关闭（不抛错）
        await run_shutdown(
            context_service=result[0],
            event_bus=result[1],
            llm_service=result[2],
            dashboard_server=result[3],
            event_recorder=result[4],
            collector_manager=result[5],
            agent_manager=result[6],
            simulator_service=simulator_service,
            sqlite_store=result[8],
            storage_ledger=result[9],
        )