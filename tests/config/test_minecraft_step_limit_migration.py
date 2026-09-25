"""移除父玩家步数上限时，验证旧配置的定向清理、版本推进与实际落盘。"""

from pathlib import Path

import pytest
import tomlkit

from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.config.upgrade import _drop_minecraft_max_steps


def test_step_limit_removal_is_idempotent_and_preserves_other_settings() -> None:
    """父玩家的旧上限被清理，子设计任务与后台查询仍使用各自配置。"""
    minecraft = {"max_steps": 50, "execute_poll_interval_ms": 4000, "builder": {"max_steps": 9}}
    data = {"agents": {"minecraft": minecraft}}
    assert _drop_minecraft_max_steps(data) == ["agents.minecraft.max_steps"]
    assert minecraft == {"execute_poll_interval_ms": 4000, "builder": {"max_steps": 9}}
    assert _drop_minecraft_max_steps(data) == []
    assert _drop_minecraft_max_steps({}) == []


@pytest.mark.parametrize("old_version", ["2.0.35", "2.0.38"])
@pytest.mark.parametrize("old_limit", [50, 120])
def test_step_limit_removal_is_written_back_only_to_agents(tmp_path: Path, old_version: str, old_limit: int) -> None:
    """存量默认值和自定义上限均从磁盘移除；重载幂等，其余配置文件保持原样。"""
    generate_default_configs(tmp_path)
    load_config_dir(tmp_path)
    agents_path = tmp_path / "agents.toml"
    agents = tomlkit.parse(agents_path.read_text(encoding="utf-8-sig"))
    agents["meta"]["version"] = old_version
    minecraft = agents["agents"]["minecraft"]
    minecraft["max_steps"] = old_limit
    minecraft["execute_poll_interval_ms"] = 4000
    minecraft["builder"]["max_steps"] = 9
    agents_path.write_text(tomlkit.dumps(agents), encoding="utf-8")
    others = {path: path.read_bytes() for path in tmp_path.glob("*.toml") if path != agents_path}

    load_config_dir(tmp_path)

    migrated = tomlkit.parse(agents_path.read_text(encoding="utf-8-sig"))
    minecraft = migrated["agents"]["minecraft"]
    assert migrated["meta"]["version"] == "2.0.40"
    assert "max_steps" not in minecraft
    assert minecraft["execute_poll_interval_ms"] == 4000 and minecraft["builder"]["max_steps"] == 9
    assert all(path.read_bytes() == content for path, content in others.items())
    written = agents_path.read_bytes()
    _, report = load_config_dir(tmp_path)
    assert not report.has_drift and agents_path.read_bytes() == written
