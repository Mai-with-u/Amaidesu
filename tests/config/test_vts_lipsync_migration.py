"""VTS lip-sync 调参键跨文件迁移测试（tools.toml v2.0.36 → infra.toml）

覆盖：
- 旧配置（[tools.avatar.vts].config 带 lip-sync 键）→ 加载 → 键搬到
  infra.toml ``[avatar.lipsync]``（开关键正名 enabled）、tools 侧删键、
  双文件版本推进；
- 用户显式值原样搬迁（不改值）；
- 二次加载零写回（幂等）。
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from src.modules.config.file_meta import CONFIG_BASELINE_VERSION
from src.modules.config.multi_file_loader import get_config_version, load_config_dir

# 搬迁的调参键（与 upgrade._LIPSYNC_KEYS 对齐的样例覆盖）
_LIPSYNC_SAMPLE = {
    "lip_sync_enabled": False,
    "sample_rate": 22050,
    "volume_threshold": 0.03,
    "max_mouth_open": 0.75,
    "min_mouth_delta": 0.008,
}


def _seed_old_vts_lipsync(config_dir: Path) -> None:
    """给 tools.toml 追加带 lip-sync 键的旧 [tools.avatar.vts] 段并拨回旧版本。

    基线模板不含 avatar.vts 段（域开关缺省不装配），直接以旧形状追加。
    """
    path = config_dir / "tools.toml"
    lines = ["\n[tools.avatar.vts]\nenabled = true\n\n[tools.avatar.vts.config]\n"]
    for key, value in _LIPSYNC_SAMPLE.items():
        rendered = "true" if value is True else "false" if value is False else repr(value)
        lines.append(f"{key} = {rendered}\n")
    content = path.read_text(encoding="utf-8-sig")
    content = content.replace(f'version = "{CONFIG_BASELINE_VERSION}"', 'version = "2.0.32"', 1)
    path.write_text(content + "".join(lines), encoding="utf-8-sig")


def test_lipsync_keys_moved_to_infra_and_written_back(tmp_path: Path):
    """旧配置构造 → 加载 → 键落 infra [avatar.lipsync]、tools 侧删键、双版本推进。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_vts_lipsync(tmp_path)

    load_config_dir(tmp_path)

    # avatar.toml 侧：键到位 + 开关正名 + 用户显式值保真。
    # 迁移钩子的目标随 avatar 迁出直接写 avatar.toml（infra 的 avatar 段
    # 已从 Schema 移除，写入即被校验剥离成数据丢失）
    avatar_doc = tomlkit.parse((tmp_path / "avatar.toml").read_text(encoding="utf-8-sig"))
    lipsync = avatar_doc["lipsync"]
    assert lipsync["enabled"] is False
    assert lipsync["sample_rate"] == 22050
    assert lipsync["volume_threshold"] == 0.03
    assert lipsync["max_mouth_open"] == 0.75
    assert lipsync["min_mouth_delta"] == 0.008

    # tools 侧：avatar 段已整体迁至 avatar.toml（2.0.38 迁移钩子）
    tools_doc = tomlkit.parse((tmp_path / "tools.toml").read_text(encoding="utf-8-sig"))
    assert "avatar" not in tools_doc["tools"]
    assert "vts" in avatar_doc["platform"]["enabled"]
    # infra 侧零残留（旧中间站段已从 Schema 移除）
    infra_doc = tomlkit.parse((tmp_path / "infra.toml").read_text(encoding="utf-8-sig"))
    assert "avatar" not in infra_doc

    # tools.toml 推进到本文件钩子链尾 target（2.0.36 迁移 + 2.0.37 删键
    # + 2.0.38 迁移）；infra.toml 保持新生成基线版本
    assert get_config_version(tmp_path, "tools.toml") == "2.0.38"
    assert get_config_version(tmp_path, "infra.toml") == CONFIG_BASELINE_VERSION


def test_migration_idempotent_on_second_load(tmp_path: Path):
    """迁移后二次加载：零漂移、零写回（幂等）。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_vts_lipsync(tmp_path)

    load_config_dir(tmp_path)
    tools_after_first = (tmp_path / "tools.toml").read_text(encoding="utf-8-sig")
    infra_after_first = (tmp_path / "infra.toml").read_text(encoding="utf-8-sig")

    _config, report = load_config_dir(tmp_path)

    assert not report.has_drift
    assert (tmp_path / "tools.toml").read_text(encoding="utf-8-sig") == tools_after_first
    assert (tmp_path / "infra.toml").read_text(encoding="utf-8-sig") == infra_after_first
