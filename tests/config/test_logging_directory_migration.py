"""[logging].directory 运行日志收进子目录迁移测试（infra.toml v2.0.42）

覆盖：
- 旧默认值（directory = "logs"）→ 加载 → 磁盘写回 "logs/app"、版本推进
- 用户显式自设的其他目录 → 原样保留不覆盖
- 新基线文件（已是 logs/app）→ 幂等零变更
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from src.modules.config.multi_file_loader import load_config_dir


def _seed_old_directory(config_dir: Path, value: str) -> None:
    """把生成的 infra.toml 的 [logging].directory 拨到指定值并回拨版本。

    生成的基线文件已带新默认 "logs/app"（Schema 默认随本批变更），迁移
    场景需要先把它拨回待迁移值；版本回拨到 2.0.41 以触发 2.0.42 钩子。
    """
    path = config_dir / "infra.toml"
    content = path.read_text(encoding="utf-8-sig")
    content = content.replace('directory = "logs/app"', f'directory = "{value}"', 1)
    content = content.replace('version = "2.0.40"', 'version = "2.0.41"', 1)
    path.write_text(content, encoding="utf-8-sig")


def test_old_default_directory_migrated(tmp_path: Path) -> None:
    """旧默认 "logs" → 加载 → 写回 "logs/app"、版本推进到 2.0.42。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_directory(tmp_path, "logs")

    load_config_dir(tmp_path)

    doc = tomlkit.parse((tmp_path / "infra.toml").read_text(encoding="utf-8-sig"))
    assert doc["logging"]["directory"] == "logs/app"
    assert doc["meta"]["version"] == "2.0.42"


def test_explicit_custom_directory_preserved(tmp_path: Path) -> None:
    """用户显式自设目录原样保留（hook 仅改写旧默认落盘值）。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_directory(tmp_path, "E:/my-logs")

    load_config_dir(tmp_path)

    doc = tomlkit.parse((tmp_path / "infra.toml").read_text(encoding="utf-8-sig"))
    assert doc["logging"]["directory"] == "E:/my-logs"


def test_migration_idempotent(tmp_path: Path) -> None:
    """迁移后的配置二次加载零写回（幂等）。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_old_directory(tmp_path, "logs")
    load_config_dir(tmp_path)

    before = (tmp_path / "infra.toml").read_text(encoding="utf-8-sig")
    load_config_dir(tmp_path)
    after = (tmp_path / "infra.toml").read_text(encoding="utf-8-sig")
    assert before == after
