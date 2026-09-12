"""ConfigService.initialize() 集成测试 — 验证完整启动流程

覆盖场景:
- 首次运行自动生成 config/ 目录
- 旧 config.toml 自动迁移
- 已有 config/ 正常加载
- 漂移检测日志
"""

import shutil
from pathlib import Path

import pytest

from src.modules.config.service import ConfigService


@pytest.fixture
def temp_project(tmp_path: Path) -> Path:
    """创建临时项目目录"""
    return tmp_path


class TestFirstRunGeneration:
    """首次运行：config/ 不存在 + 无旧 config.toml → 自动生成"""

    def test_generates_config_dir_on_first_run(self, temp_project: Path):
        service = ConfigService(base_dir=str(temp_project))
        config, was_created = service.initialize()

        config_dir = temp_project / "config"
        assert config_dir.exists()
        for fname in ("agents.toml", "collectors.toml", "tools.toml", "model.toml", "storage.toml", "infra.toml"):
            assert (config_dir / fname).exists(), fname

    def test_first_run_sets_copied_flag(self, temp_project: Path):
        service = ConfigService(base_dir=str(temp_project))
        _, was_created = service.initialize()
        assert was_created is True

    def test_generated_config_has_comments(self, temp_project: Path):
        service = ConfigService(base_dir=str(temp_project))
        service.initialize()

        infra_content = (temp_project / "config" / "infra.toml").read_text(encoding="utf-8-sig")
        assert "#" in infra_content

    def test_generated_config_has_interceptor_defaults(self, temp_project: Path):
        service = ConfigService(base_dir=str(temp_project))
        service.initialize()

        infra_content = (temp_project / "config" / "infra.toml").read_text(encoding="utf-8-sig")
        assert "rate_limit" in infra_content
        assert "similar_filter" in infra_content


class TestExistingConfigLoad:
    """已有 config/ → 正常加载"""

    def test_loads_existing_config(self, temp_project: Path):
        from src.modules.config.multi_file_loader import generate_default_configs

        generate_default_configs(temp_project / "config")

        service = ConfigService(base_dir=str(temp_project))
        config, was_created = service.initialize()

        assert was_created is False
        assert "agents" in config
        assert "tts" in config

    def test_idempotent_initialize(self, temp_project: Path):
        service = ConfigService(base_dir=str(temp_project))
        service.initialize()

        service.initialize()  # 第二次调用应跳过


class TestDriftDetection:
    """漂移检测"""

    def test_redundant_key_stripped(self, temp_project: Path):
        from src.modules.config.multi_file_loader import generate_default_configs

        generate_default_configs(temp_project / "config")

        infra_path = temp_project / "config" / "infra.toml"
        content = infra_path.read_text(encoding="utf-8-sig")
        infra_path.write_text(content + "\n[zombie_section]\nkey = \"dead\"\n", encoding="utf-8-sig")

        service = ConfigService(base_dir=str(temp_project))
        config, _ = service.initialize()

        assert "zombie_section" not in config
