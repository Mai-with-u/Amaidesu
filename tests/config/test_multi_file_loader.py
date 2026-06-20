"""多文件加载器和生成器测试"""

import shutil
from pathlib import Path

import pytest

from src.modules.config.multi_file_loader import (
    generate_default_configs,
    get_config_version,
    load_config_dir,
    needs_generation,
)


@pytest.fixture
def temp_config_dir(tmp_path):
    config_dir = tmp_path / "config"
    yield config_dir
    if config_dir.exists():
        shutil.rmtree(config_dir)


class TestGeneration:
    def test_needs_generation_for_missing_dir(self, temp_config_dir):
        assert needs_generation(temp_config_dir) is True

    def test_generate_creates_5_files(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        files = sorted(f.name for f in temp_config_dir.glob("*.toml"))
        assert files == ["core.toml", "decision.toml", "input.toml", "model.toml", "output.toml"]

    def test_needs_generation_false_after_generation(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        assert needs_generation(temp_config_dir) is False

    def test_generated_core_has_sections(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        core_content = (temp_config_dir / "core.toml").read_text(encoding="utf-8")
        assert "[meta]" in core_content
        assert "[general]" in core_content
        assert "[persona]" in core_content
        assert "[maicore]" in core_content

    def test_generated_model_has_llm(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        model_content = (temp_config_dir / "model.toml").read_text(encoding="utf-8")
        assert "[llm]" in model_content
        assert "[vlm]" in model_content


class TestLoading:
    def test_load_returns_all_categories(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        config, report = load_config_dir(temp_config_dir)
        assert "core" in config
        assert "model" in config
        assert "input" in config
        assert "decision" in config
        assert "output" in config

    def test_load_no_drift_on_generated(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        config, report = load_config_dir(temp_config_dir)
        assert not report.redundant
        assert not report.missing

    def test_load_preserves_values(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        config, _ = load_config_dir(temp_config_dir)
        assert config["core"]["general"]["platform_id"] == "amaidesu"
        assert config["core"]["persona"]["emotion_intensity"] == 7

    def test_get_config_version(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        version = get_config_version(temp_config_dir)
        assert version == "0.4.0"

    def test_drift_detection_on_stale_key(self, temp_config_dir):
        generate_default_configs(temp_config_dir)
        core_path = temp_config_dir / "core.toml"
        content = core_path.read_text(encoding="utf-8")
        content += '\n[dg_lab]\napi = "stale"\n'
        core_path.write_text(content, encoding="utf-8")
        config, report = load_config_dir(temp_config_dir)
        assert any("dg_lab" in r for r in report.redundant)
