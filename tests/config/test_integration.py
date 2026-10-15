"""ConfigService.initialize() 启动流程：首次运行生成 config/、已有目录正常加载、幂等、冗余段硬错"""

from pathlib import Path

import pytest

from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import generate_default_configs
from src.modules.config.service import ConfigService


def test_first_run_generates_commented_config_files(tmp_path: Path) -> None:
    config, was_created = ConfigService(base_dir=str(tmp_path)).initialize()
    assert was_created is True
    config_dir = tmp_path / "config"
    for name in ("agents.toml", "collectors.toml", "tools.toml", "model.toml", "storage.toml", "infra.toml"):
        assert (config_dir / name).exists(), name
    infra = (config_dir / "infra.toml").read_text(encoding="utf-8-sig")
    assert "#" in infra and "rate_limit" in infra and "similar_filter" in infra  # 带注释，含拦截器默认段


def test_existing_config_directory_loads_normally_and_initialize_is_idempotent(tmp_path: Path) -> None:
    generate_default_configs(tmp_path / "config")
    service = ConfigService(base_dir=str(tmp_path))
    config, was_created = service.initialize()
    assert was_created is False and "agents" in config and "tts" in config
    service.initialize()  # 第二次调用应跳过


def test_redundant_section_hard_fails_initialization_with_the_validation_error_as_cause(tmp_path: Path) -> None:
    generate_default_configs(tmp_path / "config")
    infra = tmp_path / "config" / "infra.toml"
    infra.write_text(infra.read_text(encoding="utf-8-sig") + '\n[zombie_section]\nkey = "dead"\n', encoding="utf-8-sig")

    with pytest.raises(RuntimeError) as exc_info:
        ConfigService(base_dir=str(tmp_path)).initialize()
    assert isinstance(exc_info.value.__cause__, ConfigValidationError)
    assert "zombie_section" in str(exc_info.value.__cause__)
