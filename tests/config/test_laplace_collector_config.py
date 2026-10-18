"""``bili_danmaku_laplace`` 配置段加载、补全与硬错语义测试。"""

from __future__ import annotations

import tomllib

import pytest

from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import generate_default_configs
from src.modules.config.service import ConfigService
from tests.config.helpers import tail_version


def _seed_collectors(config_dir, *, enabled: str, extra: str = "") -> None:
    (config_dir / "collectors.toml").write_text(
        "\n".join(
            [
                f"enabled = [{enabled}]",
                "",
                "[meta]",
                'version = "2.0.40"',
                "",
                "[bili_danmaku_laplace]",
                'url = "ws://127.0.0.1:9696"',
                extra,
            ]
        ),
        encoding="utf-8",
    )


def test_laplace_section_defaults_and_write_back(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    generate_default_configs(config_dir)
    _seed_collectors(config_dir, enabled='"console_input", "bili_danmaku_laplace"')

    service = ConfigService(base_dir=str(tmp_path))
    service.initialize()

    laplace = service.get_file_section("collectors")["bili_danmaku_laplace"]
    assert laplace["url"] == "ws://127.0.0.1:9696"
    assert laplace["token"] == ""
    assert laplace["room_id"] == 0
    assert laplace["emit_semantic_events"] is True
    assert laplace["handle_enter_messages"] is True
    assert laplace["handle_gift_messages"] is True
    assert laplace["handle_superchat_messages"] is True
    assert laplace["handle_guard_messages"] is True
    assert laplace["dedupe_cache_size"] == 2000
    assert laplace["reconnect_base_ms"] == 3000
    assert laplace["reconnect_max_ms"] == 60000

    written = tomllib.loads((config_dir / "collectors.toml").read_text(encoding="utf-8-sig"))
    assert written["meta"]["version"] == tail_version("collectors.toml")
    assert written["bili_danmaku_laplace"]["url"] == "ws://127.0.0.1:9696"


def test_laplace_unknown_key_in_registered_section_is_hard_error(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    generate_default_configs(config_dir)
    _seed_collectors(
        config_dir,
        enabled='"console_input", "bili_danmaku_laplace"',
        extra="unknown_key = 1\n",
    )

    with pytest.raises(RuntimeError) as exc_info:
        ConfigService(base_dir=str(tmp_path)).initialize()
    assert isinstance(exc_info.value.__cause__, ConfigValidationError)
    assert "unknown_key" in str(exc_info.value.__cause__)


def test_laplace_unknown_enabled_name_is_hard_error(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    generate_default_configs(config_dir)
    _seed_collectors(config_dir, enabled='"console_input", "not_a_collector"')

    with pytest.raises(RuntimeError) as exc_info:
        ConfigService(base_dir=str(tmp_path)).initialize()
    assert isinstance(exc_info.value.__cause__, ConfigValidationError)
    assert "not_a_collector" in str(exc_info.value.__cause__)
