"""Warudo 字幕三键删除迁移测试（tools.toml v2.0.37）

覆盖：
- 旧配置（[tools.avatar.warudo].config 带字幕三键）→ 加载 → 磁盘写回已删、版本推进；
- 二次加载零写回（幂等）。
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from src.modules.config.file_meta import CONFIG_BASELINE_VERSION
from src.modules.config.multi_file_loader import get_config_version, load_config_dir


def _seed_old_warudo_subtitle(config_dir: Path) -> None:
    """给 tools.toml 追加带字幕三键的旧 [tools.avatar.warudo] 段并拨回旧版本。"""
    path = config_dir / "tools.toml"
    lines = [
        "\n[tools.avatar.warudo]\nenabled = true\n\n[tools.avatar.warudo.config]\n",
        "ws_port = 19190\n",
        "subtitle_enabled = true\n",
        "subtitle_port = 8766\n",
        "subtitle_show_status = true\n",
    ]
    content = path.read_text(encoding="utf-8-sig")
    content = content.replace(f'version = "{CONFIG_BASELINE_VERSION}"', 'version = "2.0.36"', 1)
    path.write_text(content + "".join(lines), encoding="utf-8-sig")


def test_warudo_subtitle_keys_dropped_and_written_back(tmp_path: Path):
    """旧配置构造 → 加载 → 三键已删、ws_port 随段毕业迁 avatar.toml、版本推进 2.0.38。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_warudo_subtitle(tmp_path)

    load_config_dir(tmp_path)

    # 删键钩子（2.0.37）先跑，毕业钩子（2.0.38）随后把残段整体迁往 avatar.toml：
    # tools.toml 侧 [tools.avatar] 不复存在，ws_port 等存活键落在 [platform.warudo]
    tools_doc = tomlkit.parse((tmp_path / "tools.toml").read_text(encoding="utf-8-sig"))
    assert "avatar" not in tools_doc["tools"]

    avatar_doc = tomlkit.parse((tmp_path / "avatar.toml").read_text(encoding="utf-8-sig"))
    warudo_config = avatar_doc["platform"]["warudo"]
    for key in ("subtitle_enabled", "subtitle_port", "subtitle_show_status"):
        assert key not in warudo_config, f"{key} 应已删除"
    # 同段非字幕键不受影响
    assert warudo_config["ws_port"] == 19190
    assert get_config_version(tmp_path, "tools.toml") == "2.0.38"


def test_migration_idempotent_on_second_load(tmp_path: Path):
    """迁移后二次加载：零漂移、零写回（幂等）。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_warudo_subtitle(tmp_path)

    load_config_dir(tmp_path)
    content_after_first = (tmp_path / "tools.toml").read_text(encoding="utf-8-sig")

    _config, report = load_config_dir(tmp_path)

    assert not report.has_drift
    assert (tmp_path / "tools.toml").read_text(encoding="utf-8-sig") == content_after_first
