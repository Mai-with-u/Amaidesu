"""配置自动升级闭环测试（七文件布局）

覆盖 load_config_dir 的自动升级行为：
- missing 漂移 → 备份 + 写回补默认
- 冗余段/键 → 全域硬错（不再静默清理）
- 缺失文件自动补齐
- 稳定配置不重写（防抖动）
"""

import re
import shutil
from pathlib import Path

import pytest

from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import (
    generate_default_configs,
    load_config_dir,
)

REQUIRED_FILES = [
    "agents.toml",
    "collectors.toml",
    "tools.toml",
    "model.toml",
    "storage.toml",
    "infra.toml",
]


@pytest.fixture
def config_dir(tmp_path):
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    yield config_dir
    if config_dir.exists():
        shutil.rmtree(config_dir)


def _remove_section(config_dir: Path, file_name: str, section: str) -> None:
    file_path = config_dir / file_name if file_name.endswith(".toml") else config_dir / f"{file_name}.toml"
    content = file_path.read_text(encoding="utf-8-sig")
    content = re.sub(rf"\[{section}\].*?(?=\n\[|\Z)", "", content, flags=re.S)
    file_path.write_text(content, encoding="utf-8-sig")


def _append_section(config_dir: Path, file_name: str, section_toml: str) -> None:
    file_path = config_dir / file_name if file_name.endswith(".toml") else config_dir / f"{file_name}.toml"
    content = file_path.read_text(encoding="utf-8-sig")
    file_path.write_text(content + "\n" + section_toml, encoding="utf-8-sig")


class TestMissingFileFill:
    def test_missing_files_are_generated(self, config_dir: Path):
        for fname in REQUIRED_FILES[1:]:
            (config_dir / fname).unlink()

        load_config_dir(config_dir)

        for fname in REQUIRED_FILES:
            assert (config_dir / fname).exists(), f"{fname} 应被自动补齐"


class TestDriftWriteBack:
    def test_upgrade_fills_missing_fields(self, config_dir: Path):
        _remove_section(config_dir, "infra.toml", "events")

        load_config_dir(config_dir)

        assert "[events]" in (config_dir / "infra.toml").read_text(encoding="utf-8-sig")

    def test_upgrade_removes_redundant_sections(self, config_dir: Path):
        """冗余段全域硬错：手写未知段在加载期当面拦下（不再静默清理）。"""
        _append_section(config_dir, "infra.toml", '[zombie_section]\nkey = "dead"\n')

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config_dir(config_dir)
        assert "zombie_section" in str(exc_info.value)

    def test_upgrade_creates_backup(self, config_dir: Path):
        """漂移触发的写回必须先备份原文件。"""
        _remove_section(config_dir, "infra.toml", "events")

        load_config_dir(config_dir)

        backup_dir = config_dir / "old"
        batch_dirs = [p for p in backup_dir.iterdir() if p.is_dir()]
        assert len(batch_dirs) == 1
        assert (batch_dirs[0] / "infra.toml").exists()

    def test_clean_config_not_rewritten(self, config_dir: Path):
        """稳定配置不被反复重写。

        首次 load 允许 Provider 子段补全重写（引擎子段缺键时自动补全）；
        文件稳定后，再次 load 不应产生任何写回（防无限重写/抖动）。
        """
        load_config_dir(config_dir)
        content_after_first = (config_dir / "infra.toml").read_text(encoding="utf-8-sig")

        load_config_dir(config_dir)

        assert (config_dir / "infra.toml").read_text(encoding="utf-8-sig") == content_after_first


class TestAgendaToRundownMigration:
    """Agenda→Rundown 重设计：旧 agenda_* 键被 Schema 剥离，写回落盘清除。"""

    def test_agenda_keys_hard_fail_without_hook(self, config_dir: Path):
        """历史迁移（agenda_* → rundown_*）未配钩子时 → 冗余键硬错点名。

        该迁移若已配钩子则阶段②③先行搬走旧键、不会走到这里；
        本测试锁定的是"忘配钩子"的兜底语义：当面拦下而非静默清理。
        """
        _remove_section(config_dir, "agents", "agents.streamer")
        _append_section(
            config_dir,
            "agents",
            (
                "\n[agents.streamer]\n"
                "agenda_enabled = true\n"
                "agenda_path = \"config/agenda/live.toml\"\n"
                "agenda_auto_start = true\n"
                "agenda_speech_interval_ms = 5000\n"
            ),
        )

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config_dir(config_dir)
        message = str(exc_info.value)
        assert "agenda_enabled" in message
        assert "迁移钩子" in message
