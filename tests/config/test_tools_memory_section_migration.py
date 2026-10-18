"""[tools.memory] 段退役迁移测试（tools.toml v2.0.45）

通用记忆检索工具（query_memory / query_viewer_profile）退役，主播记忆工具随
主播 Agent 注册、没有独立开关。覆盖：
- 旧配置（带 [tools.memory] 段与其 config 子段）→ 加载 → 磁盘写回已删、
  版本推进链上界（当前 2.0.46）；
- 其余 [tools.*] 段不动；
- 二次加载零写回（幂等）；
- 迁移后的 tools.toml 不再认识 [tools.memory]：手写回去 = 加载期硬错。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit

from src.modules.config.multi_file_loader import generate_default_configs, get_config_version, load_config_dir


def _seed_old_tools_memory(config_dir: Path) -> None:
    """把 tools.toml 拨回 2.0.40 并补回旧的 [tools.memory] 段（关闭态，确认用户值也照删）。"""
    path = config_dir / "tools.toml"
    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    doc["meta"]["version"] = "2.0.40"
    doc["tools"]["memory"] = {"enabled": False, "config": {}}
    doc["tools"]["vision"]["enabled"] = True
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")


def test_tools_memory_section_dropped_and_written_back(tmp_path: Path) -> None:
    generate_default_configs(tmp_path)
    _seed_old_tools_memory(tmp_path)

    load_config_dir(tmp_path)

    migrated = tomlkit.parse((tmp_path / "tools.toml").read_text(encoding="utf-8-sig"))
    assert "memory" not in migrated["tools"]
    assert migrated["tools"]["vision"]["enabled"] is True  # 其他段原样保留
    assert get_config_version(tmp_path, "tools.toml") == "2.0.46"  # 链上界随新增钩子推进

    first = (tmp_path / "tools.toml").read_text(encoding="utf-8-sig")
    load_config_dir(tmp_path)
    assert (tmp_path / "tools.toml").read_text(encoding="utf-8-sig") == first


def test_memory_section_unknown_after_migration(tmp_path: Path) -> None:
    """已在 2.0.45 的文件再手写 [tools.memory]：没有钩子兜底，属未知段硬错。"""
    generate_default_configs(tmp_path)
    load_config_dir(tmp_path)
    path = tmp_path / "tools.toml"
    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    doc["tools"]["memory"] = {"enabled": True}
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")

    with pytest.raises(Exception, match="memory"):
        load_config_dir(tmp_path)
