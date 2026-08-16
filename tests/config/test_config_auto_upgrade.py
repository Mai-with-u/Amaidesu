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
        for fname in ["model.toml", "input.toml", "decision.toml", "output.toml"]:
            (config_dir / fname).unlink()

        load_config_dir(config_dir)

        for fname in REQUIRED_FILES:
            assert (config_dir / fname).exists(), f"{fname} 应被自动补齐"


class TestCrossFileMigration:
    def test_simulator_toml_merged_into_core(self, config_dir: Path):
        # 模拟旧版用户：core.toml 无 [simulator] 段 + 残留 simulator.toml（含已删除的旧字段）
        _remove_section(config_dir, "simulator")
        (config_dir / "simulator.toml").write_text(
            "[simulator]\nenabled = true\nbase_rate_per_minute = 12.0\n"
            'residents_file = "config/simulator_residents.toml"\n'
            'gifts_file = "config/simulator_gifts.toml"\n',
            encoding="utf-8-sig",
        )

        config, _ = load_config_dir(config_dir)

        assert not (config_dir / "simulator.toml").exists()
        assert config["core"]["simulator"]["enabled"] is True
        assert config["core"]["simulator"]["base_rate_per_minute"] == 12.0
        assert "residents_file" not in config["core"]["simulator"]
        assert "gifts_file" not in config["core"]["simulator"]

    def test_simulator_migration_keeps_backup(self, config_dir: Path):
        _remove_section(config_dir, "simulator")
        (config_dir / "simulator.toml").write_text(
            "[simulator]\nenabled = true\n", encoding="utf-8-sig"
        )

        load_config_dir(config_dir)

        backup_dir = config_dir / "old"
        batch_dirs = [p for p in backup_dir.iterdir() if p.is_dir()]
        assert len(batch_dirs) == 1
        assert (batch_dirs[0] / "simulator.toml").exists()

    def test_core_version_bumped_after_migration(self, config_dir: Path):
        _remove_section(config_dir, "simulator")
        (config_dir / "simulator.toml").write_text(
            "[simulator]\nenabled = true\n", encoding="utf-8-sig"
        )

        load_config_dir(config_dir)

        assert get_config_version(config_dir) == CONFIG_VERSION

    def test_existing_core_section_wins_over_leftover_source(self, config_dir: Path):
        # core.toml 已含 [simulator]（用户已配置）→ 残留 simulator.toml 仅被清理，不覆盖用户值
        (config_dir / "simulator.toml").write_text(
            "[simulator]\nenabled = true\n", encoding="utf-8-sig"
        )

        config, _ = load_config_dir(config_dir)

        assert not (config_dir / "simulator.toml").exists()
        assert config["core"]["simulator"]["enabled"] is False


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


class TestDecisionDriftWriteBack:
    """decision.toml 漂移补齐（回归：大纲功能新增字段曾因 raw dict 直读永不写回）"""

    def test_decision_missing_outline_fields_filled(self, config_dir: Path):
        """`[deciders.amaidesu]` 缺失大纲字段（模拟旧配置文件）→ 自动补默认值落盘。"""
        decision_path = config_dir / "decision.toml"
        content = decision_path.read_text(encoding="utf-8-sig")
        # 移除 outline_ 开头的所有字段行（含前一行注释），模拟升级前文件
        import re as _re

        content = _re.sub(r"# [^\n]*\n(?=outline_[a-z_]+ = )", "", content)
        content = _re.sub(r"outline_[a-z_]+ = [^\n]*\n", "", content)
        decision_path.write_text(content, encoding="utf-8-sig")

        config, report = load_config_dir(config_dir)

        # 写回后文件包含大纲字段（默认值）
        written = decision_path.read_text(encoding="utf-8-sig")
        assert "outline_enabled = false" in written
        assert 'outline_path = ""' in written
        # 加载结果包含大纲字段
        assert "outline_enabled" in config["decision"]["deciders"]["amaidesu"]
        # 写回闭环后重载无漂移（幂等）
        assert not any("outline_enabled" in m for m in report.missing)
        # 备份已创建
        backup_dir = config_dir / "old"
        batch_dirs = [p for p in backup_dir.iterdir() if p.is_dir()]
        assert any((d / "decision.toml").exists() for d in batch_dirs)

    def test_decision_clean_config_not_rewritten(self, config_dir: Path):
        """无漂移的 decision.toml 不被重写（幂等，不产生备份）。"""
        decision_path = config_dir / "decision.toml"
        content_before = decision_path.read_text(encoding="utf-8-sig")

        load_config_dir(config_dir)

        assert decision_path.read_text(encoding="utf-8-sig") == content_before
        backup_dir = config_dir / "old"
        assert not backup_dir.exists() or not list(backup_dir.rglob("decision.toml"))

    def test_decision_user_values_preserved(self, config_dir: Path):
        """补齐缺失字段时保留用户已配置的值（如 enabled 列表）。"""
        decision_path = config_dir / "decision.toml"
        content = decision_path.read_text(encoding="utf-8-sig")
        content = content.replace('enabled = ["maibot"]', 'enabled = ["amaidesu", "llm"]')
        decision_path.write_text(content, encoding="utf-8-sig")

        config, _ = load_config_dir(config_dir)

        assert config["decision"]["deciders"]["enabled"] == ["amaidesu", "llm"]


class TestInputOutputDriftWriteBack:
    """input.toml / output.toml 漂移补齐（回归：与 decision.toml 同源的 raw dict 直读）"""

    def test_input_missing_fields_filled(self, config_dir: Path):
        """`[collectors.console_input]` 缺失字段 → 自动补默认值落盘。"""
        input_path = config_dir / "input.toml"
        content = input_path.read_text(encoding="utf-8-sig")
        # 移除 console_input 段内的 user_id 行，模拟旧配置
        content = content.replace("user_id = ", "x_removed = ")
        input_path.write_text(content, encoding="utf-8-sig")

        config, report = load_config_dir(config_dir)

        written = input_path.read_text(encoding="utf-8-sig")
        assert "user_id" in written
        assert "x_removed" not in written
        assert "console_input" in config["input"]["collectors"]
        assert not any("console_input" in m for m in report.missing)

    def test_output_missing_fields_filled(self, config_dir: Path):
        """`[handlers]` 缺失元数据字段 → 自动补默认值落盘。"""
        output_path = config_dir / "output.toml"
        content = output_path.read_text(encoding="utf-8-sig")
        content = content.replace("render_timeout_ms = 10000", "render_timeout_ms = 10000\n# x_dummy = 1")
        output_path.write_text(content, encoding="utf-8-sig")

        config, report = load_config_dir(config_dir)

        written = output_path.read_text(encoding="utf-8-sig")
        assert "completion_timeout_ms" in written
        assert "handlers" in config["output"]
        assert not any("completion_timeout_ms" in m for m in report.missing)

    def test_phase_clean_config_not_rewritten(self, config_dir: Path):
        """首次升级写回后，后续加载不再重写（幂等，不产生新备份）。"""
        # 第一次加载：吸收首次升级写回（补齐缺失字段）
        load_config_dir(config_dir)
        input_before = (config_dir / "input.toml").read_text(encoding="utf-8-sig")
        output_before = (config_dir / "output.toml").read_text(encoding="utf-8-sig")
        backup_count_before = len(list((config_dir / "old").rglob("*.toml"))) if (config_dir / "old").exists() else 0

        load_config_dir(config_dir)

        assert (config_dir / "input.toml").read_text(encoding="utf-8-sig") == input_before
        assert (config_dir / "output.toml").read_text(encoding="utf-8-sig") == output_before
        backup_count_after = len(list((config_dir / "old").rglob("*.toml"))) if (config_dir / "old").exists() else 0
        assert backup_count_after == backup_count_before


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
