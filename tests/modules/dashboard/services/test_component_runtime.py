"""组件运行时启停服务：按新配置重建运行中组件、保存热应用回执、共享服务不阻碍人设生效"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agents.streamer.replyer import Replyer
from src.modules.dashboard.services.component_runtime import (
    ComponentRuntimeContext,
    affected_components,
    hot_apply_components,
    read_sub_config,
    restart_component,
)
from src.modules.prompts import PromptManager


# -------------------- 测试替身 --------------------


class _FakeCollector:
    """记录启动/停止调用的采集器替身。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.started = False
        self.start_count = 0

    async def start(self) -> None:
        self.started = True
        self.start_count += 1

    async def stop(self) -> None:
        self.started = False


class _FakeCollectorManager:
    """最小采集器管理器替身：注册表按名字索引，enable 经工厂路径换成记录配置。"""

    def __init__(self) -> None:
        self.collectors: Dict[str, _FakeCollector] = {}
        self.enable_calls: List[tuple[str, dict]] = []
        self.disable_calls: List[str] = []

    def get_collector_by_name(self, name: str) -> Optional[_FakeCollector]:
        return self.collectors.get(name)

    def register(self, collector: _FakeCollector) -> bool:
        self.collectors[collector.name] = collector
        return True

    def unregister(self, name: str) -> None:
        self.collectors.pop(name, None)

    async def start_collector(self, name: str) -> bool:
        collector = self.collectors.get(name)
        if collector is None:
            return False
        await collector.start()
        return True

    async def enable_collector(self, name: str, config: Optional[dict], event_bus: Any = None) -> bool:
        self.enable_calls.append((name, dict(config or {})))
        self.register(_FakeCollector(name))
        await self.start_collector(name)
        return True

    async def disable_collector(self, name: str) -> bool:
        self.disable_calls.append(name)
        self.unregister(name)
        return True


class _FakeAgentManager:
    """最小 Agent 管理器替身：记录 enable 入参（config / llm / prompt 管理器透传）。"""

    def __init__(self, fail_stop: bool = False) -> None:
        self.agents: Dict[str, Any] = {}
        self.enable_calls: List[dict] = []
        self.disable_calls: List[str] = []
        self.fail_stop = fail_stop

    def get_agent_by_name(self, name: str) -> Optional[Any]:
        return self.agents.get(name)

    def register(self, name: str, agent: Any) -> None:
        self.agents[name] = agent

    def unregister(self, name: str) -> None:
        self.agents.pop(name, None)

    async def start_agent(self, name: str) -> bool:
        return name in self.agents

    async def enable_agent(
        self,
        name: str,
        config: Optional[dict] = None,
        *,
        llm_manager: Any = None,
        prompt_manager: Any = None,
        event_bus: Any = None,
        **_kwargs: Any,
    ) -> bool:
        self.enable_calls.append(
            {"name": name, "config": dict(config or {}), "llm_manager": llm_manager, "prompt_manager": prompt_manager}
        )
        self.register(name, object())
        return True

    async def disable_agent(self, name: str) -> bool:
        self.disable_calls.append(name)
        if self.fail_stop:
            return False
        self.unregister(name)
        return True


class _FakeConfigService:
    def __init__(self, main_config: dict) -> None:
        self.main_config = main_config


# -------------------- affected_components：变更键 → 受影响组件 --------------------


def test_affected_components_parses_group_keys_and_ignores_enabled_meta() -> None:
    affected = affected_components(
        [
            "agents.agents.streamer.persona.bot_name",
            "agents.agents.minecraft",  # 整段更新
            "collectors.console_input.typing_interval_ms",
            "collectors.enabled",
            "agents.agents.enabled",
            "agents.meta.version",
            "infra.dashboard.port",
            "agents",
        ]
    )
    assert affected["agents"] == {"streamer", "minecraft"}
    assert affected["collectors"] == {"console_input"}


# -------------------- restart_component：停 → 重新实例化 → 启 --------------------


def test_restart_running_collector_reinstantiates_with_new_config(tmp_path: Path) -> None:
    manager = _FakeCollectorManager()
    manager.register(_FakeCollector("console_input"))
    ctx = ComponentRuntimeContext(collector_manager=manager)

    result = asyncio.run(restart_component(ctx, "collectors", "console_input", {"typing_interval_ms": 42}))

    assert result.success and result.created and not result.skipped
    assert manager.disable_calls == ["console_input"]
    assert manager.enable_calls == [("console_input", {"typing_interval_ms": 42})]
    # 重建后新实例在运行
    assert manager.get_collector_by_name("console_input").started


def test_restart_skips_components_not_running() -> None:
    manager = _FakeCollectorManager()
    ctx = ComponentRuntimeContext(collector_manager=manager)

    result = asyncio.run(restart_component(ctx, "collectors", "console_input", {}))

    assert result.success and result.skipped
    assert manager.enable_calls == []  # 未运行的只改配置，不主动拉起


def test_restart_agent_passes_fresh_config_and_shared_managers() -> None:
    llm = object()
    prompts = object()
    manager = _FakeAgentManager()
    manager.register("streamer", object())
    ctx = ComponentRuntimeContext(agent_manager=manager, llm_manager=llm, prompt_manager=prompts)

    result = asyncio.run(restart_component(ctx, "agents", "streamer", {"persona": {"bot_name": "新名字"}}))

    assert result.success and result.created
    assert manager.disable_calls == ["streamer"]
    assert len(manager.enable_calls) == 1
    call = manager.enable_calls[0]
    assert call["config"] == {"persona": {"bot_name": "新名字"}}  # 重建用磁盘新配置，不是记住的旧入参
    assert call["llm_manager"] is llm and call["prompt_manager"] is prompts  # 共享服务原样透传


def test_restart_reports_stop_failure_and_keeps_old_instance() -> None:
    manager = _FakeAgentManager(fail_stop=True)
    old_agent = object()
    manager.register("streamer", old_agent)
    ctx = ComponentRuntimeContext(agent_manager=manager)

    result = asyncio.run(restart_component(ctx, "agents", "streamer", {}))

    assert not result.success and "停止" in result.detail
    assert manager.get_agent_by_name("streamer") is old_agent  # 旧实例保留继续运行
    assert manager.enable_calls == []


# -------------------- hot_apply_components：保存管线热应用入口 --------------------


def test_hot_apply_reports_restarted_skipped_and_failures(tmp_path: Path) -> None:
    collector_manager = _FakeCollectorManager()
    collector_manager.register(_FakeCollector("console_input"))
    agent_manager = _FakeAgentManager()
    agent_manager.register("streamer", object())
    ctx = ComponentRuntimeContext(collector_manager=collector_manager, agent_manager=agent_manager)

    (tmp_path / "collectors.toml").write_text("[console_input]\ntyping_interval_ms = 99\n", encoding="utf-8")
    config_service = _FakeConfigService({"agents": {"streamer": {"persona": {"bot_name": "热应用"}}, "minecraft": {}}})

    report = asyncio.run(
        hot_apply_components(
            ctx,
            config_service,
            tmp_path,
            [
                "collectors.console_input.typing_interval_ms",
                "agents.agents.streamer.persona.bot_name",
                "agents.agents.minecraft",  # 未运行 → 跳过
            ],
        )
    )

    assert report.attempted
    assert report.restarted == ["console_input", "streamer"]
    assert report.skipped == ["minecraft"]
    assert report.failures == []
    # 采集器子段直读磁盘新值；Agent 子段读合并视图新值
    assert collector_manager.enable_calls[0][1] == {"typing_interval_ms": 99}
    assert agent_manager.enable_calls[0]["config"] == {"persona": {"bot_name": "热应用"}}


def test_hot_apply_isolated_component_failure_does_not_block_others(tmp_path: Path) -> None:
    collector_manager = _FakeCollectorManager()
    collector_manager.register(_FakeCollector("console_input"))
    agent_manager = _FakeAgentManager(fail_stop=True)
    agent_manager.register("streamer", object())
    ctx = ComponentRuntimeContext(collector_manager=collector_manager, agent_manager=agent_manager)
    config_service = _FakeConfigService({"agents": {"streamer": {}}})

    report = asyncio.run(
        hot_apply_components(
            ctx,
            config_service,
            tmp_path,
            ["collectors.console_input.typing_interval_ms", "agents.agents.streamer.persona.bot_name"],
        )
    )

    assert report.restarted == ["console_input"]  # 其他组件不受阻塞
    assert len(report.failures) == 1 and report.failures[0][0] == "streamer"


def test_read_sub_config_reads_collectors_from_disk_and_agents_from_merged_view(tmp_path: Path) -> None:
    (tmp_path / "collectors.toml").write_text('[console_input]\nmode = "disk"\n', encoding="utf-8")
    config_service = _FakeConfigService({"agents": {"streamer": {"persona": {"bot_name": "视图"}}}})

    assert read_sub_config(config_service, tmp_path, "collectors", "console_input") == {"mode": "disk"}
    assert read_sub_config(config_service, tmp_path, "agents", "streamer") == {"persona": {"bot_name": "视图"}}
    assert read_sub_config(config_service, tmp_path, "agents", "absent") == {}


# -------------------- 共享服务盘点证据：重建拿得到新人设 --------------------


def test_agent_rebuild_with_same_prompt_manager_renders_new_persona() -> None:
    """共享 PromptManager 不阻碍人设生效：person 存配置字典而非模板缓存。

    Replyer 在构造期从 config 读取人设四件套、渲染期作为变量注入模板；
    PromptManager 单例只缓存代码侧 .md 模板（配置页不可编辑）。因此
    "同一 prompt_manager + 新 config 重建实例"渲染结果跟随新配置——
    保存热应用重建 Agent 即可让人设变更生效。
    """
    prompts = PromptManager()  # 与生产同类的共享实例（测试裸构造不扫描仓库模板）
    prompts.register_scan_root(Path("src/agents/streamer/prompts"))
    prompts.load_all()
    old_llm, new_llm = object(), object()

    old_replyer = Replyer(
        config={"bot_name": "旧名字", "personality": "旧人设", "audience_salutation": "各位"},
        llm_service=old_llm,
        prompt_service=prompts,
    )
    # 模拟保存热应用的重建：共享 prompt_manager 不变，仅换新配置重新实例化
    new_replyer = Replyer(
        config={"bot_name": "新名字", "personality": "新人设", "audience_salutation": "朋友们"},
        llm_service=new_llm,
        prompt_service=prompts,
    )

    assert "旧人设" in old_replyer._render_system_prompt()
    new_system = new_replyer._render_system_prompt()
    assert "新名字" in new_system and "新人设" in new_system
    assert "旧人设" not in new_system and "旧名字" not in new_system  # 旧人设不残留——缓存的是模板，不是人设值
