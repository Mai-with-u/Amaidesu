"""台词重合度闸退役 + 台词长度预算 40 → 60 字的迁移测试（agents.toml v2.0.52）。

重合度闸的两个键从存量文件里删除；长度预算落盘值恰为旧默认 40 的改写为 60，
用户显式设置的其他值不动；重复加载零变更。
"""

from pathlib import Path

import tomlkit

from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.config.upgrade import _upgrade_streamer_speech_gate_and_budget


from tests.config.helpers import tail_version


def _write_old_layout(tmp_path: Path, budget: int, version: str = "2.0.51") -> Path:
    """把 agents.toml 拨回旧布局：闸的两键在位、长度预算落盘为给定值，版本 2.0.51。"""
    path = tmp_path / "agents.toml"
    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    doc["meta"]["version"] = version
    streamer = doc["agents"]["streamer"]
    streamer["speech_gate_window"] = 10
    streamer["speech_gate_ratio"] = 0.5
    streamer["speech_max_chars"] = budget
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return path


def test_gate_keys_removed_and_old_default_budget_rewritten(tmp_path: Path) -> None:
    """闸的两键删除、长度预算 40 改写为 60，版本推进到链上界；再次加载零变更。"""
    generate_default_configs(tmp_path)
    path = _write_old_layout(tmp_path, 40)

    load_config_dir(tmp_path)

    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    streamer = doc["agents"]["streamer"]
    assert "speech_gate_window" not in streamer
    assert "speech_gate_ratio" not in streamer
    assert streamer["speech_max_chars"] == 60
    assert doc["meta"]["version"] == tail_version("agents.toml")

    first = path.read_text(encoding="utf-8-sig")
    load_config_dir(tmp_path)
    assert path.read_text(encoding="utf-8-sig") == first


def test_user_custom_budget_untouched() -> None:
    """用户显式设置过的长度预算（非旧默认 40）原样保留。"""
    data = {"agents": {"streamer": {"speech_max_chars": 25}}}
    assert _upgrade_streamer_speech_gate_and_budget(data) == []
    assert data["agents"]["streamer"]["speech_max_chars"] == 25


def test_gate_keys_removed_even_without_budget_key() -> None:
    """没有落盘长度预算的文件（走 Schema 默认）：闸的两键照常删除，预算不动。"""
    data = {"agents": {"streamer": {"speech_gate_window": 10, "speech_gate_ratio": 0.5}}}
    changed = _upgrade_streamer_speech_gate_and_budget(data)
    assert "speech_gate_window" not in data["agents"]["streamer"]
    assert "speech_gate_ratio" not in data["agents"]["streamer"]
    assert changed == ["agents.streamer.speech_gate_window", "agents.streamer.speech_gate_ratio"]


def test_hook_is_idempotent() -> None:
    """重复执行零变更（幂等）。"""
    data = {"agents": {"streamer": {"speech_gate_window": 10, "speech_gate_ratio": 0.5, "speech_max_chars": 40}}}
    _upgrade_streamer_speech_gate_and_budget(data)
    assert _upgrade_streamer_speech_gate_and_budget(data) == []
