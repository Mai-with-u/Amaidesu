"""[events].persist 删键迁移测试（infra.toml v2.0.33）

覆盖：
- 旧布局（带 persist 键）→ 加载 → 磁盘写回后键消失、版本推进到基线
- 二次加载零写回（幂等）
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from src.modules.config.file_meta import CONFIG_BASELINE_VERSION
from src.modules.config.multi_file_loader import load_config_dir


def _rewrite_events_section(config_dir: Path) -> None:
    """把 infra.toml 的 [events] 段替换为旧布局（带 persist 键）并拨回旧版本。"""
    path = config_dir / "infra.toml"
    content = path.read_text(encoding="utf-8-sig")
    old_section = (
        "[events]\n"
        "# 事件历史内存环形缓冲大小\n"
        "history_size = 5000\n"
        "# 是否将事件历史持久化到 SQLite event_history 表\n"
        "persist = false\n"
    )
    start = content.index("[events]")
    end = content.index("[", start + 1)  # 下一段开头
    content = content[:start] + old_section + "\n" + content[end:]
    content = content.replace(f'version = "{CONFIG_BASELINE_VERSION}"', 'version = "2.0.32"', 1)
    path.write_text(content, encoding="utf-8-sig")


def test_events_persist_dropped_and_written_back(tmp_path: Path):
    """旧布局构造 → 加载 → 磁盘写回 persist 键消失、版本推进。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _rewrite_events_section(tmp_path)

    load_config_dir(tmp_path)

    doc = tomlkit.parse((tmp_path / "infra.toml").read_text(encoding="utf-8-sig"))
    events = doc["events"]
    assert "persist" not in events
    assert events["history_size"] == 5000
    # 版本流按文件独立：infra.toml 的钩子链止于 2.0.38（avatar.lipsync 迁移钩子），
    # 不随基线种子推进
    assert doc["meta"]["version"] == "2.0.38"


def test_events_persist_migration_idempotent(tmp_path: Path):
    """迁移后的配置二次加载零写回（幂等）。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _rewrite_events_section(tmp_path)

    load_config_dir(tmp_path)
    first = (tmp_path / "infra.toml").read_text(encoding="utf-8-sig")

    load_config_dir(tmp_path)
    second = (tmp_path / "infra.toml").read_text(encoding="utf-8-sig")

    assert first == second
    doc = tomlkit.parse(second)
    assert "persist" not in doc["events"]
