"""连接健康墙聚合的构建函数测试（视觉/LLM 域、TTS 多引擎、Agent 依赖聚合）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from src.modules.dashboard.api.system import (
    _build_component_items,
    _build_health_domains,
    _build_llm_items,
    _build_tts_items,
    _build_vision_items,
)


@dataclass
class FakeComponent:
    """组件清单条目替身（只带健康墙读的字段）。"""

    name: str
    is_enabled: bool = True
    is_started: bool = True


@dataclass
class FakeProvider:
    """工具提供者替身（connection_summary 契约口径）。"""

    name: str
    connected: Optional[bool]

    def connection_summary(self) -> Dict[str, Any]:
        if self.connected is None:
            return {}
        return {"is_connected": self.connected}


@dataclass
class FakeAgent:
    """Agent 替身（registered_tool_providers 口径）。"""

    name: str
    providers: list[FakeProvider] = field(default_factory=list)

    def registered_tool_providers(self) -> list[FakeProvider]:
        return self.providers


class FakeCollectorManager:
    """采集器管理器替身：list_running 口径。"""

    def __init__(self, running: list[str]) -> None:
        self._running = set(running)

    def list_running(self) -> set[str]:
        return set(self._running)

    def descriptions(self) -> Dict[str, str]:
        return {}


class FakeConfigService:
    """配置服务替身：main_config + get_file_section + base_dir（组件视图读盘用）。"""

    def __init__(self, files: Dict[str, Dict[str, Any]], main_config: Dict[str, Any], base_dir: Any = None) -> None:
        self._files = files
        self.main_config = main_config
        self.base_dir = str(base_dir) if base_dir is not None else ""

    def get_file_section(self, file_stem: str, default: Any = None) -> Dict[str, Any]:
        return self._files.get(file_stem, default if default is not None else {})


def _write_collectors_toml(base_dir: Any, enabled: list[str]) -> None:
    """把 collectors.toml 写进替身配置目录（组件视图 build_config_view 从盘读）。"""
    config_dir = Path(base_dir) / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    names = ", ".join(f'"{name}"' for name in enabled)
    (config_dir / "collectors.toml").write_text(
        f'enabled = [{names}]\n\n[live_frame]\nsource = "obs"\n', encoding="utf-8"
    )


@dataclass
class FakeServer:
    """DashboardServer 替身：只带健康墙读的成员。"""

    config_service: Optional[FakeConfigService] = None
    agent_manager: Optional[Any] = None
    collector_manager: Optional[Any] = None
    tool_registry: Optional[Any] = None


class FakeTtsObservation:
    """TTS 观测接口替身（get_stats 契约口径）。"""

    def __init__(self, connected: bool) -> None:
        self._connected = connected

    def get_stats(self) -> Dict[str, Any]:
        return {"is_connected": self._connected, "name": "gptsovits"}


MODEL_FILES: Dict[str, Dict[str, Any]] = {
    "model": {
        "llm_profiles": {
            "planner": {"model_list": ["ds-main", "ds-backup"]},
            "replyer": {"model_list": ["kimi-main"]},
        },
        "llm_models": [
            {"name": "ds-main", "api_provider": "deepseek", "visual": True},
            {"name": "ds-backup", "api_provider": "deepseek"},
            {"name": "kimi-main", "api_provider": "moonshot"},
        ],
        "llm_providers": [
            {"name": "deepseek", "api_key": "sk-test"},
            {"name": "moonshot", "api_key": ""},
        ],
    },
}


def _agent_manager() -> Any:
    """带一个 minecraft Agent（挂一个断开的 MCP 提供者）的管理器替身。"""

    class FakeManager:
        def get_agent_by_name(self, name: str) -> Optional[FakeAgent]:
            if name == "minecraft":
                return FakeAgent(name, [FakeProvider("maicraft", connected=False)])
            if name == "streamer":
                return FakeAgent(name, [FakeProvider("streamer", connected=None)])
            return None

        def list_running(self) -> set[str]:
            return {"minecraft", "streamer"}

        def descriptions(self) -> Dict[str, str]:
            return {}

    return FakeManager()


def test_tts_domain_lists_all_engines_with_disabled_for_inactive() -> None:
    server = FakeServer(
        config_service=FakeConfigService({"infra": {"tts": {"enabled": True, "provider": "gptsovits"}}}, {})
    )
    items = _build_tts_items(server, FakeTtsObservation(connected=False))
    by_name = {item.name: item for item in items}
    assert set(by_name) >= {"edge_tts", "gptsovits", "voicebox", "omni_tts"}
    assert by_name["gptsovits"].state == "offline"
    assert by_name["edge_tts"].state == "disabled"
    assert by_name["omni_tts"].state == "disabled"


def test_tts_domain_disabled_when_master_switch_off() -> None:
    server = FakeServer(
        config_service=FakeConfigService({"infra": {"tts": {"enabled": False, "provider": "gptsovits"}}}, {})
    )
    items = _build_tts_items(server, FakeTtsObservation(connected=True))
    assert all(item.state == "disabled" for item in items)


def test_tts_domain_unknown_when_engine_not_assembled() -> None:
    server = FakeServer(
        config_service=FakeConfigService({"infra": {"tts": {"enabled": True, "provider": "gptsovits"}}}, {})
    )
    items = _build_tts_items(server, None)
    states = {item.name: item.state for item in items}
    assert states["gptsovits"] == "unknown"
    assert states["edge_tts"] == "disabled"


def _vision_server(tmp_path: Any, frame_enabled: bool = True, frame_started: bool = True) -> FakeServer:
    collectors_files = {
        "collectors": {"enabled": ["live_frame"] if frame_enabled else [], "live_frame": {"source": "obs"}}
    }
    main_config: Dict[str, Any] = {
        "agents": {"enabled": ["streamer"], "streamer": {"live_frame": {"enabled": True}}},
    }
    _write_collectors_toml(tmp_path, ["live_frame"] if frame_enabled else [])
    return FakeServer(
        config_service=FakeConfigService(
            {"collectors": collectors_files, **MODEL_FILES}, main_config, base_dir=tmp_path
        ),
        collector_manager=FakeCollectorManager(["live_frame"] if frame_started else []),
    )


def test_vision_domain_ok_when_chain_complete(tmp_path: Any) -> None:
    items = _build_vision_items(_vision_server(tmp_path))
    by_name = {item.name: item for item in items}
    assert by_name["live_frame"].state == "ok"
    assert by_name["vision_perception"].state == "ok"


def test_vision_perception_disabled_when_frame_collector_off(tmp_path: Any) -> None:
    items = _build_vision_items(_vision_server(tmp_path, frame_enabled=False, frame_started=False))
    by_name = {item.name: item for item in items}
    assert by_name["live_frame"].state == "disabled"
    assert by_name["vision_perception"].state == "disabled"


def test_vision_perception_disabled_when_primary_model_not_visual(tmp_path: Any) -> None:
    server = _vision_server(tmp_path)
    assert server.config_service is not None
    model = dict(MODEL_FILES["model"])
    model["llm_models"] = [
        {"name": "ds-main", "api_provider": "deepseek", "visual": False},
        {"name": "ds-backup", "api_provider": "deepseek"},
        {"name": "kimi-main", "api_provider": "moonshot"},
    ]
    server.config_service._files = {**server.config_service._files, "model": model}
    items = _build_vision_items(server)
    by_name = {item.name: item for item in items}
    assert by_name["live_frame"].state == "ok"
    assert by_name["vision_perception"].state == "disabled"


def _agents_server(tmp_path: Any) -> FakeServer:
    _write_collectors_toml(tmp_path, [])
    return FakeServer(
        config_service=FakeConfigService(
            {"collectors": {"enabled": []}},
            {"agents": {"enabled": ["minecraft", "streamer"], "minecraft": {}, "streamer": {}}},
            base_dir=tmp_path,
        ),
        agent_manager=_agent_manager(),
        collector_manager=FakeCollectorManager([]),
    )


def test_agent_item_aggregates_broken_dependency(tmp_path: Any) -> None:
    items = _build_component_items(_agents_server(tmp_path), "agents")
    by_name = {item.name: item for item in items}
    # minecraft 依赖的 MCP 提供者断开 → 条目红
    assert by_name["minecraft"].state == "offline"
    assert "maicraft" in by_name["minecraft"].detail
    # streamer 的本地提供者连接不可见（unknown 依赖）不抬高条目；TTS 引擎在线也不抬
    assert by_name["streamer"].state == "ok"


def test_agent_item_streamer_red_when_tts_disconnected(tmp_path: Any) -> None:
    server = _agents_server(tmp_path)
    items = _build_component_items(server, "agents", FakeTtsObservation(connected=False))
    by_name = {item.name: item for item in items}
    assert by_name["streamer"].state == "offline"
    assert "tts" in by_name["streamer"].detail


def test_agent_disabled_item_not_overridden_by_dependencies(tmp_path: Any) -> None:
    server = _agents_server(tmp_path)
    # 配置未启用（不在 enabled 名单）→ disabled，依赖断开不把它改成红
    assert server.config_service is not None
    server.config_service.main_config = {"agents": {"enabled": ["streamer"], "minecraft": {}, "streamer": {}}}
    items = _build_component_items(server, "agents")
    by_name = {item.name: item for item in items}
    assert by_name["minecraft"].state == "disabled"


def test_llm_domain_states_per_profile() -> None:
    server = FakeServer(config_service=FakeConfigService(dict(MODEL_FILES), {}))
    by_name = {item.name: item for item in _build_llm_items(server)}
    # planner：主备都注册且密钥已填 → ok
    assert by_name["planner"].state == "ok"
    # replyer：主模型密钥未配置 → disabled（配置上就没开）
    assert by_name["replyer"].state == "disabled"


def test_llm_domain_offline_when_primary_model_unregistered() -> None:
    model = dict(MODEL_FILES["model"])
    model["llm_profiles"] = {"planner": {"model_list": ["ghost", "ds-backup"]}}
    server = FakeServer(config_service=FakeConfigService({"model": model}, {}))
    items = _build_llm_items(server)
    assert items[0].state == "offline"
    assert "ghost" in items[0].detail


def test_llm_domain_degraded_when_backup_model_unregistered() -> None:
    model = dict(MODEL_FILES["model"])
    model["llm_profiles"] = {"planner": {"model_list": ["ds-main", "ghost"]}}
    server = FakeServer(config_service=FakeConfigService({"model": model}, {}))
    items = _build_llm_items(server)
    assert items[0].state == "degraded"
    assert "ghost" in items[0].detail


def test_domain_builder_degrades_single_domain_failure_to_unknown() -> None:
    class FailingConfigService:
        """读取即抛的配置服务替身：验证单域探查失败只降级该域。"""

        main_config: Dict[str, Any] = {}

        def get_file_section(self, file_stem: str, default: Any = None) -> Dict[str, Any]:
            raise RuntimeError("配置读取故障")

    server = FakeServer(config_service=FailingConfigService())
    domains = _build_health_domains(server, None)
    assert domains["llm"][0].state == "unknown"
