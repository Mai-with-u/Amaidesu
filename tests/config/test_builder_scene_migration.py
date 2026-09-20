"""建造器接入现有场景操作时，只迁移旧默认绑定并实际写回配置。"""

from pathlib import Path

import pytest
import tomlkit

from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.config.upgrade import _upgrade_builder_scene_transport


def test_scene_transport_migration_is_idempotent_and_preserves_custom_submission() -> None:
    """不同校验工具不再装配，已有自定义受理工具名仍由用户配置决定。"""
    builder = {"validate_tool": "builder_validate", "preview_tool": "", "execute_tool": "custom_execute"}
    data = {"agents": {"minecraft": {"builder": builder}}}
    assert _upgrade_builder_scene_transport(data)
    assert builder == {"execute_tool": "custom_execute"}
    assert _upgrade_builder_scene_transport(data) == []


def test_incompatible_custom_design_binding_fails_without_mutation() -> None:
    """自定义旧校验协议不能被猜测替换，报错时原配置保持完整。"""
    builder = {"validate_tool": "my_validate", "preview_tool": "", "execute_tool": "builder_execute"}
    data = {"agents": {"minecraft": {"builder": builder}}}
    with pytest.raises(ValueError, match="自定义旧协议"):
        _upgrade_builder_scene_transport(data)
    assert builder["validate_tool"] == "my_validate" and builder["execute_tool"] == "builder_execute"


def test_scene_transport_written_back_only_advances_agents_version(tmp_path: Path) -> None:
    """真实加载旧配置，改写默认绑定并补查询工具，其他文件版本与模型选择保持原值。"""
    generate_default_configs(tmp_path)
    agents_path, model_path = tmp_path / "agents.toml", tmp_path / "model.toml"
    agents = tomlkit.parse(agents_path.read_text(encoding="utf-8-sig"))
    model = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
    agents["meta"]["version"] = model["meta"]["version"] = "2.0.34"
    agents["agents"]["minecraft"]["builder"] = {
        "validate_tool": "builder_validate",
        "preview_tool": "",
        "execute_tool": "builder_execute",
        "max_steps": 9,
    }
    agents_path.write_text(tomlkit.dumps(agents), encoding="utf-8")
    model_path.write_text(tomlkit.dumps(model), encoding="utf-8")
    load_config_dir(tmp_path)
    agents = tomlkit.parse(agents_path.read_text(encoding="utf-8-sig"))
    model = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
    builder = agents["agents"]["minecraft"]["builder"]
    assert builder["execute_tool"] == "maicraft_execute" and builder["task_tool"] == "maicraft_task"
    assert "validate_tool" not in builder and "preview_tool" not in builder
    assert builder["max_steps"] == 9
    assert agents["meta"]["version"] == "2.0.35" and model["meta"]["version"] == "2.0.34"
    _, report = load_config_dir(tmp_path)
    assert not report.missing
