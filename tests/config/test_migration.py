"""旧配置迁移测试（v2.0.0：7 文件）"""

import shutil
from pathlib import Path

import pytest
import tomlkit

from src.modules.config.migration import migrate_old_config


@pytest.fixture
def old_config(tmp_path):
    config_path = tmp_path / "config.toml"
    doc = tomlkit.document()

    doc["meta"] = {"version": "0.3.0"}
    doc["general"] = {"platform_id": "test_platform"}
    doc["persona"] = {"bot_name": "test_bot", "emotion_intensity": 5}
    doc["llm"] = {"client": "openai", "model": "test-model", "api_key": "sk-test"}
    doc["collectors"] = {"enabled": ["console_input"]}
    doc["deciders"] = {"active": "llm", "available": ["llm", "maibot"]}
    doc["handlers"] = {"enabled": ["subtitle"], "concurrent_rendering": True}
    doc["pipelines"] = {
        "rate_limit": {"priority": 100, "enabled": True, "global_rate_limit": 100},
        "profanity_filter": {"output": {"priority": 100, "enabled": True, "words": ["bad"]}},
    }
    doc["dg_lab"] = {"api_base_url": "http://localhost:8081"}
    doc["spark_rtasr"] = {"app_id": ""}

    config_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    yield config_path
    if config_path.exists():
        config_path.unlink()


class TestMigration:
    def test_migrates_to_7_files(self, tmp_path, old_config):
        config_dir = tmp_path / "config"
        report = migrate_old_config(old_config, config_dir)
        assert (config_dir / "core.toml").exists()
        assert (config_dir / "model.toml").exists()
        for fname in ("agents.toml", "tools.toml"):
            assert not (config_dir / fname).exists(), (
                f"{fname} 在用户没显式配置时不写入，由 load_config_dir 自动补齐"
            )
        for fname in ("memory.toml", "storage.toml", "background.toml"):
            assert not (config_dir / fname).exists()

    def test_drops_dead_configs(self, tmp_path, old_config):
        config_dir = tmp_path / "config"
        report = migrate_old_config(old_config, config_dir)
        assert "dg_lab" in report.dropped_sections
        assert "spark_rtasr" in report.dropped_sections
        core_content = (config_dir / "core.toml").read_text(encoding="utf-8")
        assert "dg_lab" not in core_content
        assert "spark_rtasr" not in core_content

    def test_preserves_user_values(self, tmp_path, old_config):
        config_dir = tmp_path / "config"
        migrate_old_config(old_config, config_dir)
        core_content = (config_dir / "core.toml").read_text(encoding="utf-8")
        assert "test_platform" in core_content
        assert "test_bot" in core_content
        model_content = (config_dir / "model.toml").read_text(encoding="utf-8")
        assert "test-model" in model_content

    def test_creates_backup(self, tmp_path, old_config):
        config_dir = tmp_path / "config"
        report = migrate_old_config(old_config, config_dir)
        assert report.backup_path is not None
        assert report.backup_path.exists()
        assert (config_dir / "old").exists()

    def test_reports_migrated_sections(self, tmp_path, old_config):
        config_dir = tmp_path / "config"
        report = migrate_old_config(old_config, config_dir)
        assert len(report.migrated_sections) > 0
        assert any("meta" in s for s in report.migrated_sections)
        assert any("llm" in s for s in report.migrated_sections)

    def test_migrates_deciders_active_to_enabled(self, tmp_path, old_config):
        """迁移时 deciders.active → deciders.enabled（写入 decision.toml，兼容期保留）。"""
        config_dir = tmp_path / "config"
        migrate_old_config(old_config, config_dir)
        decision_content = (config_dir / "decision.toml").read_text(encoding="utf-8")
        assert "enabled" in decision_content
        assert "llm" in decision_content
        assert '"active"' not in decision_content
        assert '"available"' not in decision_content

    def test_migrates_pipelines_to_phase_first(self, tmp_path, old_config):
        config_dir = tmp_path / "config"
        migrate_old_config(old_config, config_dir)
        core_content = (config_dir / "core.toml").read_text(encoding="utf-8")
        assert "input" in core_content
        assert "output" in core_content
        assert "rate_limit" in core_content
        assert "profanity_filter" in core_content