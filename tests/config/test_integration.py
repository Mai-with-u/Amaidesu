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
        assert (config_dir / "core.toml").exists()
        assert (config_dir / "model.toml").exists()
        assert (config_dir / "input.toml").exists()
        assert (config_dir / "decision.toml").exists()
        assert (config_dir / "output.toml").exists()

    def test_first_run_sets_copied_flag(self, temp_project: Path):
        service = ConfigService(base_dir=str(temp_project))
        _, was_created = service.initialize()
        assert was_created is True

    def test_generated_config_has_comments(self, temp_project: Path):
        service = ConfigService(base_dir=str(temp_project))
        service.initialize()

        core_content = (temp_project / "config" / "core.toml").read_text(encoding="utf-8")
        assert "#" in core_content

    def test_generated_config_has_pipeline_defaults(self, temp_project: Path):
        service = ConfigService(base_dir=str(temp_project))
        service.initialize()

        core_content = (temp_project / "config" / "core.toml").read_text(encoding="utf-8")
        assert "rate_limit" in core_content
        assert "similar_filter" in core_content


class TestOldConfigMigration:
    """旧 config.toml 存在 → 自动迁移到 config/"""

    def test_migrates_old_config(self, temp_project: Path):
        old_config = temp_project / "config.toml"
        old_config.write_text(
            '[meta]\nversion = "0.3.0"\n\n'
            '[general]\nplatform_id = "test_platform"\n\n'
            '[persona]\nbot_name = "测试名"\n\n'
            '[llm]\nclient = "openai"\nmodel = "gpt-4"\ntemperature = 0.2\n'
            'api_key = ""\nbase_url = "https://api.openai.com/v1"\n'
            'max_tokens = 1024\nmax_retries = 3\nretry_delay = 1.0\n\n'
            '[collectors]\nenabled = []\n\n'
            '[deciders]\nactive = "maibot"\navailable = ["maibot"]\n\n'
            '[handlers]\nenabled = ["subtitle"]\n'
            'concurrent_rendering = true\nerror_handling = "continue"\n'
            'render_timeout = 10.0\n\n'
            '[dg_lab]\napi_base_url = "http://dead.example.com"\n',
            encoding="utf-8",
        )

        service = ConfigService(base_dir=str(temp_project))
        config, was_created = service.initialize()

        config_dir = temp_project / "config"
        assert config_dir.exists()
        assert (config_dir / "core.toml").exists()
        assert (config_dir / "model.toml").exists()

        backup_dir = config_dir / "old"
        assert backup_dir.exists()
        backups = list(backup_dir.glob("config_*.toml"))
        assert len(backups) == 1

    def test_migration_preserves_user_values(self, temp_project: Path):
        old_config = temp_project / "config.toml"
        old_config.write_text(
            '[meta]\nversion = "0.3.0"\n\n'
            '[general]\nplatform_id = "my_platform"\n\n'
            '[persona]\nbot_name = "自定义名字"\n\n'
            '[llm]\nclient = "openai"\nmodel = "gpt-4"\ntemperature = 0.2\n'
            'api_key = ""\nbase_url = "https://api.openai.com/v1"\n'
            'max_tokens = 1024\nmax_retries = 3\nretry_delay = 1.0\n\n'
            '[collectors]\nenabled = []\n\n'
            '[deciders]\nactive = "maibot"\navailable = ["maibot"]\n\n'
            '[handlers]\nenabled = []\n'
            'concurrent_rendering = true\nerror_handling = "continue"\n'
            'render_timeout = 10.0\n',
            encoding="utf-8",
        )

        service = ConfigService(base_dir=str(temp_project))
        config, _ = service.initialize()

        assert config.get("general", {}).get("platform_id") == "my_platform"
        assert config.get("persona", {}).get("bot_name") == "自定义名字"

    def test_migration_drops_dead_config(self, temp_project: Path):
        old_config = temp_project / "config.toml"
        old_config.write_text(
            '[meta]\nversion = "0.3.0"\n\n'
            '[general]\nplatform_id = "test"\n\n'
            '[llm]\nclient = "openai"\nmodel = "gpt-4"\ntemperature = 0.2\n'
            'api_key = ""\nbase_url = "https://api.openai.com/v1"\n'
            'max_tokens = 1024\nmax_retries = 3\nretry_delay = 1.0\n\n'
            '[dg_lab]\napi_base_url = "dead"\n',
            encoding="utf-8",
        )

        service = ConfigService(base_dir=str(temp_project))
        service.initialize()

        core_content = (temp_project / "config" / "core.toml").read_text(encoding="utf-8")
        assert "dg_lab" not in core_content


class TestExistingConfigLoad:
    """已有 config/ → 正常加载"""

    def test_loads_existing_config(self, temp_project: Path):
        from src.modules.config.multi_file_loader import generate_default_configs

        generate_default_configs(temp_project / "config")

        service = ConfigService(base_dir=str(temp_project))
        config, was_created = service.initialize()

        assert was_created is False
        assert "general" in config
        assert "persona" in config

    def test_idempotent_initialize(self, temp_project: Path):
        service = ConfigService(base_dir=str(temp_project))
        service.initialize()

        service.initialize()  # 第二次调用应跳过


class TestDriftDetection:
    """漂移检测"""

    def test_redundant_key_stripped(self, temp_project: Path):
        from src.modules.config.multi_file_loader import generate_default_configs

        generate_default_configs(temp_project / "config")

        core_path = temp_project / "config" / "core.toml"
        content = core_path.read_text(encoding="utf-8")
        core_path.write_text(content + '\n[zombie_section]\nkey = "dead"\n', encoding="utf-8")

        service = ConfigService(base_dir=str(temp_project))
        config, _ = service.initialize()

        assert "zombie_section" not in config
