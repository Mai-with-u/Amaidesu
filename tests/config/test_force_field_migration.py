"""force 段字段正名迁移测试（agents.toml v2.0.32）

覆盖：
- 旧字段名（force_data_types / force_importance）→ 加载 → 磁盘写回新字段名
- 版本推进到基线；二次加载零写回（幂等）
- 版本缺口硬错已在 test_upgrade.py::TestVersionPipeline::test_missing_version_hard_fail
  覆盖（ConfigValidationError），此处不重复
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from src.modules.config.file_meta import CONFIG_BASELINE_VERSION
from src.modules.config.multi_file_loader import get_config_version, load_config_dir


def _rewrite_force_section(config_dir: Path, old_section: str) -> None:
    """把 agents.toml 的 force 段替换为旧字段布局并拨回旧版本。"""
    path = config_dir / "agents.toml"
    content = path.read_text(encoding="utf-8-sig")
    start = content.index("[agents.streamer.force]")
    end = content.index("[agents.streamer.proactive]")
    content = content[:start] + old_section + "\n" + content[end:]
    content = content.replace(f'version = "{CONFIG_BASELINE_VERSION}"', 'version = "2.0.31"', 1)
    path.write_text(content, encoding="utf-8-sig")


OLD_FORCE_SECTION = (
    "[agents.streamer.force]\n"
    "# 强制响应的数据类型\n"
    'force_data_types = ["super_chat", "guard", "gift"]\n'
    "# importance 达到该值则强制响应\n"
    "force_importance = 0.8\n"
)


def test_force_fields_migrated_and_written_back(tmp_path: Path):
    """旧字段名构造 → 加载 → 磁盘写回新字段名、死字段清除、版本推进。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _rewrite_force_section(tmp_path, OLD_FORCE_SECTION)

    load_config_dir(tmp_path)

    doc = tomlkit.parse((tmp_path / "agents.toml").read_text(encoding="utf-8-sig"))
    force = doc["agents"]["streamer"]["force"]
    assert "force_data_types" not in force
    assert "force_importance" not in force
    assert force["force_message_types"] == ["super_chat", "guard", "gift"]
    # agents.toml 版本流独立：只推进到本文件最后一个钩子 target，
    # 不随其他文件（如 tools.toml 2.0.36）前进而前进
    assert get_config_version(tmp_path, "agents.toml") == "2.0.40"


def test_migration_idempotent_on_second_load(tmp_path: Path):
    """迁移后二次加载：零漂移、零写回（幂等）。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _rewrite_force_section(tmp_path, OLD_FORCE_SECTION)

    load_config_dir(tmp_path)
    content_after_first = (tmp_path / "agents.toml").read_text(encoding="utf-8-sig")

    _config2, report2 = load_config_dir(tmp_path)

    assert not report2.has_drift
    assert (tmp_path / "agents.toml").read_text(encoding="utf-8-sig") == content_after_first
