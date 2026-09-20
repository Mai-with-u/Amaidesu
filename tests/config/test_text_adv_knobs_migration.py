"""text_adv 假旋钮删字段迁移测试（agents.toml v2.0.34）

覆盖：
- 旧配置（带 engine_kind / decision_strategy / enable_event_emission）→ 加载
  → 磁盘写回已删三个旧键、新增运行字段补默认值、版本推进
- 二次加载零写回（幂等）
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from src.modules.config.file_meta import CONFIG_BASELINE_VERSION
from src.modules.config.multi_file_loader import get_config_version, load_config_dir


def _seed_old_text_adv(config_dir: Path) -> None:
    """把 agents.toml 的 text_adv 段替换回旧字段布局并拨回旧版本。"""
    path = config_dir / "agents.toml"
    content = path.read_text(encoding="utf-8-sig")
    old_section = (
        "[agents.text_adv]\n"
        "# 内容引擎标识\n"
        'engine_kind = "text_adv"\n'
        "# 推进策略\n"
        'decision_strategy = "first_option"\n'
        "# 是否 emit 事件\n"
        "enable_event_emission = true\n"
    )
    start = content.index("[agents.text_adv]")
    content = content[:start] + old_section
    content = content.replace(f'version = "{CONFIG_BASELINE_VERSION}"', 'version = "2.0.33"', 1)
    path.write_text(content, encoding="utf-8-sig")


def test_text_adv_knobs_dropped_and_new_fields_written_back(tmp_path: Path):
    """旧字段构造 → 加载 → 磁盘写回旧键删除、新字段补默认值、版本推进。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_text_adv(tmp_path)

    load_config_dir(tmp_path)

    doc = tomlkit.parse((tmp_path / "agents.toml").read_text(encoding="utf-8-sig"))
    text_adv = doc["agents"]["text_adv"]
    for key in ("engine_kind", "decision_strategy", "enable_event_emission"):
        assert key not in text_adv
    assert text_adv["monitor_index"] == 1
    assert text_adv["keys"]["advance"] == "space"
    assert text_adv["keys"]["skip"] == "ctrl"
    assert text_adv["keys"]["menu"] == "backspace"
    assert text_adv["stability_sample_ms"] == 150
    assert text_adv["stability_consecutive"] == 2
    # None 默认字段 TOML 不可表达，落盘为缺省不写
    assert "region" not in text_adv
    assert "auto_button_xy" not in text_adv
    # agents.toml 版本流独立：只推进到本文件最后一个钩子 target，
    # 不随其他文件（如 tools.toml 2.0.36）前进而前进
    assert get_config_version(tmp_path, "agents.toml") == "2.0.35"


def test_text_adv_migration_idempotent_on_second_load(tmp_path: Path):
    """迁移后二次加载：零漂移、零写回（幂等）。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_text_adv(tmp_path)

    load_config_dir(tmp_path)
    content_after_first = (tmp_path / "agents.toml").read_text(encoding="utf-8-sig")

    _config, report = load_config_dir(tmp_path)

    assert not report.has_drift
    assert (tmp_path / "agents.toml").read_text(encoding="utf-8-sig") == content_after_first
