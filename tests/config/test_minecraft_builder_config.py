"""建造配置纯新增字段的落盘补全，保留现有游戏模型和逐文件版本。"""

from pathlib import Path
from typing import Any

import pytest
import tomlkit

from src.agents.minecraft.config import MinecraftConfig
from src.modules.config.model_schemas import LLMProfilesConfig
from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir


def test_missing_builder_profile_uses_existing_minecraft_models() -> None:
    """用户没有名为 default 的模型时，新增设计用途仍引用已有游戏模型。"""
    profiles = LLMProfilesConfig(minecraft={"model_list": ["custom-game"], "max_tokens": 1234})
    assert profiles.minecraft_builder.model_list == ["custom-game"]
    assert profiles.minecraft.max_tokens == 1234
    assert profiles.minecraft_builder.max_tokens != profiles.minecraft.max_tokens


def test_explicit_builder_profile_is_preserved() -> None:
    """明确选择的设计模型不能被游戏模型的默认继承覆盖。"""
    profiles = LLMProfilesConfig(
        minecraft={"model_list": ["game"]}, minecraft_builder={"model_list": ["designer"], "max_tokens": 5678}
    )
    assert profiles.minecraft_builder.model_list == ["designer"]
    assert profiles.minecraft_builder.max_tokens == 5678


@pytest.mark.parametrize("builder", [{"max_steps": 0}, {"task_timeout_ms": 0}, {"retained_jobs": 0}])
def test_invalid_builder_budgets_fail_validation(builder: dict[str, Any]) -> None:
    """预算不能退化成无边界循环。"""
    with pytest.raises(ValueError):
        MinecraftConfig(builder=builder)


def test_added_builder_fields_written_without_version_bump(tmp_path: Path) -> None:
    """模拟旧安装缺少建造段，实际加载后补齐落盘且保留用户模型与各文件版本。"""
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    agents_path, model_path = config_dir / "agents.toml", config_dir / "model.toml"
    agents = tomlkit.parse(agents_path.read_text(encoding="utf-8-sig"))
    model = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
    agent_version, model_version = agents["meta"]["version"], model["meta"]["version"]
    del agents["agents"]["minecraft"]["builder"]
    del model["llm_profiles"]["minecraft_builder"]
    model["llm_models"][0]["name"] = "my-existing-model"
    for profile in model["llm_profiles"].values():
        profile["model_list"] = ["my-existing-model"]
    agents_path.write_text(tomlkit.dumps(agents), encoding="utf-8")
    model_path.write_text(tomlkit.dumps(model), encoding="utf-8")

    load_config_dir(config_dir)

    written_agents = tomlkit.parse(agents_path.read_text(encoding="utf-8-sig"))
    written_model = tomlkit.parse(model_path.read_text(encoding="utf-8-sig"))
    assert written_agents["agents"]["minecraft"]["builder"]["enabled"] is True
    assert written_model["llm_profiles"]["minecraft_builder"]["model_list"] == ["my-existing-model"]
    assert written_agents["meta"]["version"] == agent_version
    assert written_model["meta"]["version"] == model_version
    _, repeated_report = load_config_dir(config_dir)
    assert not repeated_report.missing
