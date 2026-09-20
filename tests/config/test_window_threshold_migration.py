"""window_event_threshold 死配置清除迁移测试（agents.toml v2.0.33）

覆盖：
- 旧配置（带 window_event_threshold）→ 加载 → 磁盘写回已删该键、版本推进
- 二次加载零写回（幂等）
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from src.modules.config.file_meta import CONFIG_BASELINE_VERSION
from src.modules.config.multi_file_loader import get_config_version, load_config_dir


def _seed_old_window_threshold(config_dir: Path) -> None:
    """把 agents.toml 的 background 段加回 window_event_threshold 并拨回旧版本。"""
    path = config_dir / "agents.toml"
    content = path.read_text(encoding="utf-8-sig")
    marker = "[agents.streamer.background]\n"
    old_block = marker + "window_event_threshold = 200\n"
    assert marker in content
    content = content.replace(marker, old_block, 1)
    content = content.replace(f'version = "{CONFIG_BASELINE_VERSION}"', 'version = "2.0.32"', 1)
    path.write_text(content, encoding="utf-8-sig")


def test_window_threshold_dropped_and_written_back(tmp_path: Path):
    """旧配置构造 → 加载 → 磁盘写回已删该键、版本推进到 2.0.33。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_window_threshold(tmp_path)

    load_config_dir(tmp_path)

    doc = tomlkit.parse((tmp_path / "agents.toml").read_text(encoding="utf-8-sig"))
    background = doc["agents"]["streamer"]["background"]
    assert "window_event_threshold" not in background
    # agents.toml 版本流独立：只推进到本文件最后一个钩子 target，
    # 不随其他文件（如 tools.toml 2.0.36）前进而前进
    assert get_config_version(tmp_path, "agents.toml") == "2.0.35"


def test_migration_idempotent_on_second_load(tmp_path: Path):
    """迁移后二次加载：零漂移、零写回（幂等）。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_window_threshold(tmp_path)

    load_config_dir(tmp_path)
    content_after_first = (tmp_path / "agents.toml").read_text(encoding="utf-8-sig")

    _config, report = load_config_dir(tmp_path)

    assert not report.has_drift
    assert (tmp_path / "agents.toml").read_text(encoding="utf-8-sig") == content_after_first
