"""配置自动升级闭环测试

覆盖 load_config_dir 的自动升级行为（对齐 MaiBot 的写回闭环）：
- 版本不一致 → 执行 upgrade hooks → 写回并更新 [meta].version
- 漂移（缺失/冗余字段）→ 备份 + 写回
- 缺失文件自动补齐
- 用户 mcp 配置不再被静默吞噬
"""

import re
import shutil
from pathlib import Path

import pytest

from src.modules.config.multi_file_loader import (
    CONFIG_VERSION,
    generate_default_configs,
    get_config_version,
    load_config_dir,
)

REQUIRED_FILES = [
    "core.toml",
    "model.toml",
    "input.toml",
    "decision.toml",
    "output.toml",
    "simulator.toml",
]


@pytest.fixture
def config_dir(tmp_path):
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    yield config_dir
    if config_dir.exists():
        shutil.rmtree(config_dir)


def _set_version(config_dir: Path, version: str) -> None:
    core_path = config_dir / "core.toml"
    content = core_path.read_text(encoding="utf-8-sig")
    content = re.sub(r'version = "[^"]+"', f'version = "{version}"', content, count=1)
    core_path.write_text(content, encoding="utf-8-sig")


def _remove_section(config_dir: Path, section: str) -> None:
    core_path = config_dir / "core.toml"
    content = core_path.read_text(encoding="utf-8-sig")
    content = re.sub(rf"\[{section}\].*?(?=\n\[|\Z)", "", content, flags=re.S)
    core_path.write_text(content, encoding="utf-8-sig")


def _append_section(config_dir: Path, section_toml: str) -> None:
    core_path = config_dir / "core.toml"
    content = core_path.read_text(encoding="utf-8-sig")
    core_path.write_text(content + "\n" + section_toml, encoding="utf-8-sig")


class TestMissingFileFill:
    def test_missing_files_are_generated(self, config_dir: Path):
        for fname in ["model.toml", "input.toml", "decision.toml", "output.toml", "simulator.toml"]:
            (config_dir / fname).unlink()

        load_config_dir(config_dir)

        for fname in REQUIRED_FILES:
            assert (config_dir / fname).exists(), f"{fname} 应被自动补齐"


class TestVersionUpgrade:
    def test_version_mismatch_triggers_writeback_and_updates_version(self, config_dir: Path):
        _set_version(config_dir, "0.3.0")

        load_config_dir(config_dir)

        assert get_config_version(config_dir) == CONFIG_VERSION

    def test_second_load_does_not_rewrite(self, config_dir: Path):
        _set_version(config_dir, "0.3.0")
        load_config_dir(config_dir)
        backup_dir = config_dir / "old"
        backups_after_first = len(list(backup_dir.rglob("*.toml"))) if backup_dir.exists() else 0
        content_before = (config_dir / "core.toml").read_text(encoding="utf-8-sig")

        load_config_dir(config_dir)

        assert (config_dir / "core.toml").read_text(encoding="utf-8-sig") == content_before
        backups_after_second = len(list(backup_dir.rglob("*.toml"))) if backup_dir.exists() else 0
        assert backups_after_second == backups_after_first


class TestDriftWriteBack:
    def test_upgrade_fills_missing_fields(self, config_dir: Path):
        _remove_section(config_dir, "events")

        load_config_dir(config_dir)

        assert "[events]" in (config_dir / "core.toml").read_text(encoding="utf-8-sig")

    def test_upgrade_removes_redundant_sections(self, config_dir: Path):
        _append_section(config_dir, '[zombie_section]\nkey = "dead"\n')

        load_config_dir(config_dir)

        assert "zombie_section" not in (config_dir / "core.toml").read_text(encoding="utf-8-sig")

    def test_upgrade_creates_backup(self, config_dir: Path):
        _set_version(config_dir, "0.3.0")

        load_config_dir(config_dir)

        backup_dir = config_dir / "old"
        batch_dirs = [p for p in backup_dir.iterdir() if p.is_dir()]
        assert len(batch_dirs) == 1
        assert (batch_dirs[0] / "core.toml").exists()

    def test_same_batch_shares_backup_directory(self, config_dir: Path):
        _set_version(config_dir, "0.3.0")
        model_path = config_dir / "model.toml"
        content = model_path.read_text(encoding="utf-8-sig")
        model_path.write_text(content + '\n[zombie_model]\nkey = "dead"\n', encoding="utf-8-sig")

        load_config_dir(config_dir)

        backup_dir = config_dir / "old"
        batch_dirs = [p for p in backup_dir.iterdir() if p.is_dir()]
        assert len(batch_dirs) == 1
        assert (batch_dirs[0] / "core.toml").exists()
        assert (batch_dirs[0] / "model.toml").exists()

    def test_clean_config_not_rewritten(self, config_dir: Path):
        content_before = (config_dir / "core.toml").read_text(encoding="utf-8-sig")

        load_config_dir(config_dir)

        assert (config_dir / "core.toml").read_text(encoding="utf-8-sig") == content_before


class TestMCPConfig:
    def test_mcp_config_preserved(self, config_dir: Path):
        core_path = config_dir / "core.toml"
        content = core_path.read_text(encoding="utf-8-sig")
        content = re.sub(r"(\[mcp\]\n(?:#[^\n]*\n)*enabled = )false", r"\1true", content)
        core_path.write_text(content, encoding="utf-8-sig")

        config, _ = load_config_dir(config_dir)

        assert config["core"]["mcp"]["enabled"] is True

    def test_redundant_mcp_fields_cleaned(self, config_dir: Path):
        import tomlkit

        core_path = config_dir / "core.toml"
        content = core_path.read_text(encoding="utf-8-sig")
        mcp_idx = content.index("\n[mcp]")
        next_table_idx = content.find("\n[", mcp_idx + 4)
        insert_pos = next_table_idx + 1 if next_table_idx != -1 else len(content)
        content = content[:insert_pos] + 'cors_origins = ["http://x"]\n' + content[insert_pos:]
        content = re.sub(r"(\[mcp\]\n(?:#[^\n]*\n)*enabled = )false", r"\1true", content)
        core_path.write_text(content, encoding="utf-8-sig")

        load_config_dir(config_dir)

        doc = tomlkit.parse((config_dir / "core.toml").read_text(encoding="utf-8-sig")).unwrap()
        assert "cors_origins" not in doc["mcp"]
        assert doc["mcp"]["enabled"] is True
