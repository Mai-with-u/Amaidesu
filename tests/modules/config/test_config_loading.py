"""配置加载：Schema 默认值与主配置合并、agents 启用名单、deep_merge 规则，以及历史重构遗留的契约守卫"""

import inspect

import pytest
from pydantic import BaseModel

from src.modules.config import schemas
from src.modules.config.core_schemas import DiagnosticsConfig
from src.modules.config.service import ConfigService, deep_merge_configs


class InputProviderSchema(BaseModel):
    enabled: bool = True
    priority: int = 100
    message_prefix: str = ""
    max_messages: int = 10
    custom_field: str = "default_value"


def _service(base_dir) -> ConfigService:
    service = ConfigService(base_dir=str(base_dir))
    service.initialize()
    return service


def test_schema_defaults_apply_and_unknown_phase_or_uninitialized_service_yield_nothing(tmp_path) -> None:
    (tmp_path / "config.toml").write_text(
        '[general]\nplatform_id = "test"\n\n[collectors]\nenabled = ["test_input"]\n', encoding="utf-8"
    )
    service = _service(tmp_path)
    merged = service.get_config_with_defaults("test_input", "input", InputProviderSchema)
    assert merged == {
        "enabled": True,
        "priority": 100,
        "message_prefix": "",
        "max_messages": 10,
        "custom_field": "default_value",
    }
    assert service.get_config_with_defaults("test_component", "invalid_phase") == {}

    assert (
        ConfigService(base_dir=str(tmp_path / "other")).get_config_with_defaults("test_input", "input") == {}
    )  # 未初始化


def test_agents_enabled_list_keeps_the_users_choice(tmp_path) -> None:
    """取值必须是 AgentType 已知名，文件必须自带 [meta].version（每文件版本硬性要求）。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agents.toml").write_text(
        '[meta]\nversion = "2.0.31"\n\n[agents]\nenabled = ["streamer", "minecraft"]\n', encoding="utf-8"
    )
    assert _service(tmp_path).get_section("agents", {}).get("enabled") == ["streamer", "minecraft"]


def test_deep_merge_overrides_scalars_recurses_dicts_replaces_lists_and_ignores_none() -> None:
    merged = deep_merge_configs(
        {"a": 1, "b": 2, "nested": {"x": 10, "y": 20}, "items": [1, 2, 3]},
        {"b": None, "c": 3, "nested": {"y": 200, "z": 30}, "items": [4, 5]},
    )
    assert merged == {"a": 1, "b": 2, "c": 3, "nested": {"x": 10, "y": 200, "z": 30}, "items": [4, 5]}  # None 不覆盖
    assert deep_merge_configs({"a": 1, "b": 2}, {"b": 20, "c": 3}) == {"a": 1, "b": 20, "c": 3}


def test_historical_refactor_contracts() -> None:
    assert "list_all_providers" not in schemas.__all__  # __all__ 里的名称必须实际定义
    assert all(hasattr(schemas, name) for name in schemas.__all__)
    assert "phase" in inspect.signature(ConfigService.get_config_with_defaults).parameters  # 签名用 phase 而非 layer
    assert "layer" not in inspect.signature(ConfigService.get_config_with_defaults).parameters
    assert "phase" in inspect.signature(ConfigService.load_global_overrides).parameters


def test_diagnostics_keep_forever_by_default_and_reject_negative_retention() -> None:
    """诊断正文是场次回看的事件侧事实源，默认不滚动删除；显式正整数保留天数才启用清理。"""
    default = DiagnosticsConfig()
    assert (default.enabled, default.retention_days) == (True, 0)
    assert DiagnosticsConfig(retention_days=7).retention_days == 7
    with pytest.raises(ValueError):
        DiagnosticsConfig(retention_days=-1)
