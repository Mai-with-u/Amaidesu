"""配置自动升级闭环测试（v2.0.0：7 文件）

覆盖 load_config_dir 的自动升级行为：
- 版本不一致 → 执行 upgrade hook → 写回并更新 [meta].version
- 漂移（缺失/冗余字段）→ 备份 + 写回
- 缺失文件自动补齐
- 旧段迁移：[collectors] → [tools.perception.config]、[handlers] → [tools.output.config]、
  [deciders] → [agents]，由 CrossFileMigration + upgrade hook 协作完成
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
    "agents.toml",
    "tools.toml",
    "memory.toml",
    "storage.toml",
    "background.toml",
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
        for fname in ["model.toml", "agents.toml", "tools.toml", "memory.toml", "storage.toml", "background.toml"]:
            (config_dir / fname).unlink()

        load_config_dir(config_dir)

        for fname in REQUIRED_FILES:
            assert (config_dir / fname).exists(), f"{fname} 应被自动补齐"


class TestCrossFileMigration:
    """v2.0.0 跨文件迁移：旧 input/decision/output.toml → 新 tools/agents.toml"""

    def test_input_toml_merged_into_tools_perception(self, config_dir: Path):
        """旧 input.toml 的 [collectors] → tools.toml 的 [tools.perception.config]。"""
        _remove_section(config_dir, "tools", "tools")  # 移除 tools 段，模拟缺失
        (config_dir / "input.toml").write_text(
            "[collectors]\nenabled = [\"console_input\"]\n\n"
            "[collectors.console_input]\nuser_id = \"u1\"\n",
            encoding="utf-8-sig",
        )

        config, _ = load_config_dir(config_dir)

        assert not (config_dir / "input.toml").exists()
        assert "console_input" in config["tools"]["tools"]["perception"]["config"]

    def test_decision_toml_merged_into_agents(self, config_dir: Path):
        """旧 decision.toml 的 [deciders] → agents.toml 的 [agents]。"""
        _remove_section(config_dir, "agents", "agents")
        (config_dir / "decision.toml").write_text(
            "[deciders]\nenabled = [\"amaidesu\"]\n",
            encoding="utf-8-sig",
        )

        config, _ = load_config_dir(config_dir)

        assert not (config_dir / "decision.toml").exists()
        assert "amaidesu" in config["agents"]["agents"]["enabled"]

    def test_core_version_bumped_after_migration(self, config_dir: Path):
        (config_dir / "input.toml").write_text(
            "[collectors]\nenabled = []\n", encoding="utf-8-sig"
        )

        load_config_dir(config_dir)

        assert get_config_version(config_dir) == CONFIG_VERSION

    def test_legacy_file_keeps_backup(self, config_dir: Path):
        (config_dir / "decision.toml").write_text(
            "[deciders]\nenabled = []\n", encoding="utf-8-sig"
        )

        load_config_dir(config_dir)

        backup_dir = config_dir / "old"
        batch_dirs = [p for p in backup_dir.iterdir() if p.is_dir()]
        assert any((d / "decision.toml").exists() for d in batch_dirs)


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
        _remove_section(config_dir, "core.toml", "events")

        load_config_dir(config_dir)

        assert "[events]" in (config_dir / "core.toml").read_text(encoding="utf-8-sig")

    def test_upgrade_removes_redundant_sections(self, config_dir: Path):
        _append_section(config_dir, "core.toml", '[zombie_section]\nkey = "dead"\n')

        load_config_dir(config_dir)

        assert "zombie_section" not in (config_dir / "core.toml").read_text(encoding="utf-8-sig")

    def test_upgrade_creates_backup(self, config_dir: Path):
        _set_version(config_dir, "0.3.0")

        load_config_dir(config_dir)

        backup_dir = config_dir / "old"
        batch_dirs = [p for p in backup_dir.iterdir() if p.is_dir()]
        assert len(batch_dirs) == 1
        assert (batch_dirs[0] / "core.toml").exists()

    def test_clean_config_not_rewritten(self, config_dir: Path):
        content_before = (config_dir / "core.toml").read_text(encoding="utf-8-sig")

        load_config_dir(config_dir)

        assert (config_dir / "core.toml").read_text(encoding="utf-8-sig") == content_before


class TestCoreUpgradeHook2_0_0:
    """core.toml 2.0.0：删 [maicore] + 改造 [context] 为 ContextAssembler"""

    def test_maicore_removed_during_upgrade(self, config_dir: Path):
        _append_section(config_dir, "core.toml", "[maicore]\nport = 9999\n")
        _set_version(config_dir, "0.5.4")

        load_config_dir(config_dir)

        assert "maicore" not in (config_dir / "core.toml").read_text(encoding="utf-8-sig")

    def test_context_transformed_to_assembler(self, config_dir: Path):
        """旧 [context]（会话存储字段）→ 新 ContextAssembler 配置。"""
        _remove_section(config_dir, "core.toml", "context")
        _append_section(
            config_dir,
            "core.toml",
            "[context]\nstorage_type = \"memory\"\nmax_messages_per_session = 200\n",
        )
        _set_version(config_dir, "0.5.4")

        load_config_dir(config_dir)

        content = (config_dir / "core.toml").read_text(encoding="utf-8-sig")
        assert "memory_recall_viewers" in content
        assert "max_messages_per_session" not in content


class TestModelUpgradeHook2_0_0:
    """model.toml 2.0.0：llm_outline → llm_agenda"""

    def test_llm_outline_renamed(self, config_dir: Path):
        _append_section(
            config_dir,
            "model.toml",
            "[llm_outline]\nprovider = \"default\"\nmodel = \"old-model\"\n",
        )
        _set_version(config_dir, "0.5.4")

        load_config_dir(config_dir)

        content = (config_dir / "model.toml").read_text(encoding="utf-8-sig")
        assert "[llm_agenda]" in content
        assert "[llm_outline]" not in content


class TestIdempotentMigration:
    """迁移 + 写回闭环必须幂等：二次加载不再触发额外漂移/迁移"""

    def test_input_migration_idempotent(self, config_dir: Path):
        _remove_section(config_dir, "tools", "tools")
        (config_dir / "input.toml").write_text(
            "[collectors]\nenabled = [\"x\"]\n\n[collectors.x]\nkey = 1\n",
            encoding="utf-8-sig",
        )
        load_config_dir(config_dir)
        written_after_first = (config_dir / "tools.toml").read_text(encoding="utf-8-sig")
        backup_count_first = len(list((config_dir / "old").rglob("*.toml")))

        load_config_dir(config_dir)

        assert (config_dir / "tools.toml").read_text(encoding="utf-8-sig") == written_after_first
        backup_count_second = len(list((config_dir / "old").rglob("*.toml")))
        assert backup_count_second == backup_count_first


class TestCrossFileMigrationSafety:
    """A1.5 回归保护：跨文件迁移只在源段真实存在且合并成功时删除源文件。"""

    def test_input_without_collectors_preserves_file(self, config_dir: Path):
        """input.toml 不含 [collectors] 段（只有 [pipelines.input.rate_limit]）→ 原文件保留，无备份。"""
        _remove_section(config_dir, "core.toml", "pipelines")
        (config_dir / "input.toml").write_text(
            "[pipelines.input.rate_limit]\n"
            "priority = 100\n"
            "enabled = true\n"
            "global_rate_limit = 100\n",
            encoding="utf-8-sig",
        )
        backup_dir = config_dir / "old"
        backups_before = (
            len(list(backup_dir.rglob("*.toml"))) if backup_dir.exists() else 0
        )

        load_config_dir(config_dir)

        assert (config_dir / "input.toml").exists(), "源文件应原样保留"
        original = (config_dir / "input.toml").read_text(encoding="utf-8-sig")
        assert "[pipelines.input.rate_limit]" in original
        assert "global_rate_limit = 100" in original

        backups_after = (
            len(list(backup_dir.rglob("*.toml"))) if backup_dir.exists() else 0
        )
        assert backups_after == backups_before, "不应产生 input.toml 备份"

    def test_input_with_collectors_and_pipelines_migrates_then_removes(
        self, config_dir: Path
    ):
        """input.toml 同时含 [collectors] 和 [pipelines] → collectors 迁入 tools.toml，
        源文件按既有语义备份后删除，备份文件中包含 [pipelines] 内容。"""
        _remove_section(config_dir, "core.toml", "pipelines")
        (config_dir / "input.toml").write_text(
            "[pipelines.input.rate_limit]\n"
            "priority = 100\n"
            "enabled = true\n"
            "global_rate_limit = 100\n"
            "\n"
            "[collectors]\n"
            "enabled = [\"console_input\"]\n"
            "\n"
            "[collectors.console_input]\n"
            "user_id = \"console_user\"\n",
            encoding="utf-8-sig",
        )

        load_config_dir(config_dir)

        assert not (config_dir / "input.toml").exists(), "源文件应被备份后删除"

        backup_dir = config_dir / "old"
        input_backups = list(backup_dir.rglob("input.toml"))
        assert len(input_backups) >= 1, "input.toml 应有备份"
        backup_content = input_backups[0].read_text(encoding="utf-8-sig")
        assert "[pipelines.input.rate_limit]" in backup_content
        assert "global_rate_limit = 100" in backup_content
        assert "[collectors.console_input]" in backup_content
        assert "user_id = \"console_user\"" in backup_content

        config, _ = load_config_dir(config_dir)
        perception_cfg = config["tools"]["tools"].get("perception") or {}
        perception_config = perception_cfg.get("config", {}) if isinstance(perception_cfg, dict) else {}
        assert "console_input" in perception_config
        assert perception_config["console_input"]["user_id"] == "console_user"


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