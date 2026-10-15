"""视觉配置由提供者 Schema 生成与写回；历史残留（退役采集器段、enabled 里的未知名）按全域硬错语义在加载期拦下"""

from __future__ import annotations

import pytest

from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.config.tools_schemas import VisionProviderConfig
from src.modules.vision.look_at_screen import LookAtScreenProvider


@pytest.fixture
def config_dir(tmp_path):
    directory = tmp_path / "config"
    generate_default_configs(directory)
    return directory


def _edit(path, edit) -> None:
    original = path.read_text(encoding="utf-8-sig")
    changed = edit(original)
    assert changed != original
    path.write_text(changed, encoding="utf-8-sig")


def test_vision_config_reuses_the_provider_schema_and_materializes_defaults_on_disk(config_dir) -> None:
    config = VisionProviderConfig().config
    provider_schema = LookAtScreenProvider.ConfigSchema()
    assert isinstance(config, LookAtScreenProvider.ConfigSchema)
    assert config.monitor_index == provider_schema.monitor_index == 1 and config.default_region is None
    assert "vlm_timeout_ms" not in config.model_dump() and config.default_max_width == 1280

    _, report = load_config_dir(config_dir)  # 首启生成的字段都已落盘，生成端与写回端形态一致
    assert not report.has_drift
    tools = (config_dir / "tools.toml").read_text(encoding="utf-8-sig")
    assert "monitor_index = 1" in tools and "default_max_width = 1280" in tools and 'type = "vision"' in tools
    assert "vlm_timeout_ms" not in tools and "default_region" not in tools  # None 字段 TOML 不可表达，不落盘


def test_user_values_are_preserved_and_unknown_fields_hard_fail(config_dir) -> None:
    tools_path = config_dir / "tools.toml"
    _edit(tools_path, lambda text: text.replace("monitor_index = 1", "monitor_index = 2"))
    config, _ = load_config_dir(config_dir)
    vision = config["tools"]["tools"]["vision"]["config"]
    assert vision["monitor_index"] == 2 and vision["default_max_width"] == 1280 and "vlm_timeout_ms" not in vision
    assert "monitor_index = 2" in tools_path.read_text(encoding="utf-8-sig")  # 写回不丢用户值

    _edit(tools_path, lambda text: text.replace("monitor_index = 2", 'monitor_index = 2\nunknown_field = "x"'))
    with pytest.raises(ConfigValidationError, match="unknown_field"):  # 冗余全域硬错，不再静默剥离
        load_config_dir(config_dir)


def test_retired_collector_section_and_unknown_enabled_names_hard_fail_with_the_valid_list(config_dir) -> None:
    collectors = config_dir / "collectors.toml"
    _edit(collectors, lambda text: text + "\n[screen]\nenabled = true\n")  # 已退役 screen 采集器的历史段名
    with pytest.raises(ConfigValidationError) as exc_info:
        load_config_dir(config_dir)
    assert "screen" in str(exc_info.value) and "console_input" in str(exc_info.value)  # 合法名单随错误给出

    def append_enabled(extra: str):
        def edit(text: str) -> str:
            lines = text.splitlines(keepends=True)
            (index,) = [i for i, line in enumerate(lines) if line.strip().startswith("enabled = [")]
            names = lines[index].strip()[len("enabled = [") : -1]
            lines[index] = f"enabled = [{names}, {extra}]\n" if names.strip() else f"enabled = [{extra}]\n"
            return "".join(lines)

        return edit

    for name in ("screen", "typo_collector_xyz"):  # 未知名不限退役名，任意拼写错同样硬错
        fresh = config_dir.parent / f"fresh_{name}"
        generate_default_configs(fresh)
        _edit(fresh / "collectors.toml", append_enabled(f'"{name}"'))
        with pytest.raises(ConfigValidationError) as exc_info:
            load_config_dir(fresh)
        assert name in str(exc_info.value)
