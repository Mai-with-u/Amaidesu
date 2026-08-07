"""组件摘要测试：get_component_summaries 必须包含全部已注册组件（含未启用）。

组件管理页需要展示所有组件（含未启用），未启用的组件
``is_enabled=False``、``is_started=False``，供前端渲染"已禁用"卡片并启用。
"""

import pytest

# 导入三个阶段包触发全部 @collector/@decider/@handler 注册
import src.stages.input.collectors  # noqa: F401
import src.stages.decision.deciders  # noqa: F401
import src.stages.output.handlers  # noqa: F401

from src.stages.input.manager import InputCollectorManager
from src.stages.decision.manager import DeciderManager
from src.stages.output.manager import OutputHandlerManager
from src.stages.input.registry import list_collectors
from src.stages.decision.registry import list_deciders
from src.stages.output.registry import list_handlers


def _empty_input_manager() -> InputCollectorManager:
    return InputCollectorManager(event_bus=None, pipeline_manager=None)


def _empty_output_manager() -> OutputHandlerManager:
    return OutputHandlerManager(event_bus=None, pipeline_manager=None)


def _empty_decision_manager() -> DeciderManager:
    return DeciderManager(event_bus=None)


def test_input_summaries_cover_all_registered_collectors():
    """空 Manager（无实例化组件）也应返回全部已注册 Collector，且均为未启用。"""
    summaries = _empty_input_manager().get_component_summaries()
    assert {s["name"] for s in summaries} == set(list_collectors())
    for s in summaries:
        assert s["phase"] == "input"
        assert s["is_enabled"] is False
        assert s["is_started"] is False


def test_output_summaries_cover_all_registered_handlers():
    """空 Manager（无实例化组件）也应返回全部已注册 Handler，且均为未启用。"""
    summaries = _empty_output_manager().get_component_summaries()
    assert {s["name"] for s in summaries} == set(list_handlers())
    for s in summaries:
        assert s["phase"] == "output"
        assert s["is_enabled"] is False
        assert s["is_started"] is False


def test_decision_summaries_cover_all_registered_deciders():
    """空 Manager（无实例化组件）也应返回全部已注册 Decider，且均为未启用。"""
    summaries = _empty_decision_manager().get_component_summaries()
    assert {s["name"] for s in summaries} == set(list_deciders())
    for s in summaries:
        assert s["phase"] == "decision"
        assert s["is_enabled"] is False
        assert s["is_started"] is False


def test_enable_via_config_appends_to_enabled_list(tmp_path):
    """未启用组件点"启动"→ _enable_via_config 应把组件名追加到 TOML enabled 列表。"""
    from types import SimpleNamespace

    from src.modules.dashboard.api.components import _enable_via_config

    toml_path = tmp_path / "input.toml"
    toml_path.write_text('[collectors]\nenabled = ["console_input"]\n', encoding="utf-8")
    server = SimpleNamespace(get_config_path=lambda section: str(toml_path))

    resp = _enable_via_config(server, "input", "stt")

    assert resp.success is True
    assert "stt" in resp.message
    import tomlkit

    doc = tomlkit.parse(toml_path.read_text(encoding="utf-8"))
    assert doc["collectors"]["enabled"] == ["console_input", "stt"]


def test_enable_via_config_idempotent(tmp_path):
    """重复启用同一组件不应重复追加。"""
    from types import SimpleNamespace

    from src.modules.dashboard.api.components import _enable_via_config

    toml_path = tmp_path / "input.toml"
    toml_path.write_text('[collectors]\nenabled = ["stt"]\n', encoding="utf-8")
    server = SimpleNamespace(get_config_path=lambda section: str(toml_path))

    resp = _enable_via_config(server, "input", "stt")

    assert resp.success is True
    import tomlkit

    doc = tomlkit.parse(toml_path.read_text(encoding="utf-8"))
    assert doc["collectors"]["enabled"] == ["stt"]


def test_disable_via_config_removes_from_enabled_list(tmp_path):
    """停用组件应从 TOML enabled 列表移除。"""
    from types import SimpleNamespace

    from src.modules.dashboard.api.components import _disable_via_config

    toml_path = tmp_path / "input.toml"
    toml_path.write_text('[collectors]\nenabled = ["console_input", "stt"]\n', encoding="utf-8")
    server = SimpleNamespace(get_config_path=lambda section: str(toml_path))

    resp = _disable_via_config(server, "input", "stt")

    assert resp.success is True
    import tomlkit

    doc = tomlkit.parse(toml_path.read_text(encoding="utf-8"))
    assert doc["collectors"]["enabled"] == ["console_input"]


def test_disable_via_config_idempotent(tmp_path):
    """停用不在列表中的组件应幂等成功。"""
    from types import SimpleNamespace

    from src.modules.dashboard.api.components import _disable_via_config

    toml_path = tmp_path / "input.toml"
    toml_path.write_text('[collectors]\nenabled = ["console_input"]\n', encoding="utf-8")
    server = SimpleNamespace(get_config_path=lambda section: str(toml_path))

    resp = _disable_via_config(server, "input", "stt")

    assert resp.success is True
    import tomlkit

    doc = tomlkit.parse(toml_path.read_text(encoding="utf-8"))
    assert doc["collectors"]["enabled"] == ["console_input"]


@pytest.mark.asyncio
async def test_decision_dynamic_enable_disable():
    """Decider 动态启用（加入并行分发）与停用（移除）应生效。"""
    from src.modules.events.event_bus import EventBus

    manager = DeciderManager(event_bus=EventBus())

    ok = await manager.enable_decider("command", {})
    assert ok is True
    assert "command" in manager.get_decider_names()
    assert manager._decider_ready.get("command") is True

    # 幂等：再次启用不报错
    ok_again = await manager.enable_decider("command", {})
    assert ok_again is True

    ok_stop = await manager.disable_decider("command")
    assert ok_stop is True
    assert "command" not in manager.get_decider_names()
    assert "command" not in manager._decider_ready


@pytest.mark.asyncio
async def test_decision_enable_failure_on_unknown_decider():
    """启用未注册的 Decider 应返回 False 而不抛异常。"""
    from src.modules.events.event_bus import EventBus

    manager = DeciderManager(event_bus=EventBus())
    ok = await manager.enable_decider("not_a_real_decider", {})
    assert ok is False
