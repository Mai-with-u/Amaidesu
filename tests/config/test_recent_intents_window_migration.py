"""已聊话题回看窗默认 6 → 60 轮的迁移测试（agents.toml v2.0.47）。

旧默认落盘值改写为新默认；用户显式设置的其他值不动；重复加载零变更。
"""

from pathlib import Path

import tomlkit

from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.config.upgrade import _widen_recent_intents_window


def _write_old_default(tmp_path: Path, value: int, version: str = "2.0.46") -> Path:
    """把 agents.toml 拨回旧布局：话题回看窗落盘为旧默认 6，版本 2.0.46。"""
    path = tmp_path / "agents.toml"
    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    doc["meta"]["version"] = version
    doc["agents"]["streamer"]["planner_recent_intents_max"] = value
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return path


def test_old_default_rewritten_and_written_back(tmp_path: Path) -> None:
    """旧默认 6 改写为 60，版本推进到 2.0.47；再次加载零变更。"""
    generate_default_configs(tmp_path)
    path = _write_old_default(tmp_path, 6)

    load_config_dir(tmp_path)

    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    assert doc["agents"]["streamer"]["planner_recent_intents_max"] == 60
    assert doc["meta"]["version"] == "2.0.50"

    first = path.read_text(encoding="utf-8-sig")
    load_config_dir(tmp_path)
    assert path.read_text(encoding="utf-8-sig") == first


def test_user_custom_value_untouched() -> None:
    """用户显式设置过的值（非旧默认 6）原样保留。"""
    data = {"agents": {"streamer": {"planner_recent_intents_max": 12}}}
    assert _widen_recent_intents_window(data) == []
    assert data["agents"]["streamer"]["planner_recent_intents_max"] == 12
    assert _widen_recent_intents_window({"agents": {"streamer": {}}}) == []
