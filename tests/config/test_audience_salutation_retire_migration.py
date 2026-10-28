"""persona.audience_salutation 死配置清除迁移测试（agents.toml v2.0.50）

覆盖：
- 旧配置（带该键）→ 加载 → 磁盘写回已删该键、版本推进
- 二次加载零写回（幂等）
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from src.modules.config.multi_file_loader import get_config_version, load_config_dir


def _seed_old_audience_salutation(config_dir: Path) -> None:
    """把 agents.toml 的 persona 段加回 audience_salutation 并拨回旧版本。"""
    path = config_dir / "agents.toml"
    content = path.read_text(encoding="utf-8-sig")
    marker = "[agents.streamer.persona]\n"
    old_block = marker + 'audience_salutation = "大家"\n'
    assert marker in content
    content = content.replace(marker, old_block, 1)
    path.write_text(content, encoding="utf-8-sig")


def test_persona_audience_salutation_dropped_and_written_back(tmp_path: Path):
    """旧配置构造 → 加载 → 磁盘写回已删该键、版本推进到链上界。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_audience_salutation(tmp_path)

    load_config_dir(tmp_path)

    doc = tomlkit.parse((tmp_path / "agents.toml").read_text(encoding="utf-8-sig"))
    persona = doc["agents"]["streamer"]["persona"]
    assert "audience_salutation" not in persona
    assert get_config_version(tmp_path, "agents.toml") == "2.0.50"


def test_migration_is_idempotent(tmp_path: Path):
    """再次加载零写回：钩子对已迁移数据不产生变更。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_audience_salutation(tmp_path)
    load_config_dir(tmp_path)
    first_mtime = (tmp_path / "agents.toml").stat().st_mtime_ns

    load_config_dir(tmp_path)

    assert (tmp_path / "agents.toml").stat().st_mtime_ns == first_mtime, "二次加载不应再写盘"
    assert get_config_version(tmp_path, "agents.toml") == "2.0.50"
