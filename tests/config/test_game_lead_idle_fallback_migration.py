"""游戏主控冷场兜底默认时长 50_000 → 0 毫秒的迁移测试（agents.toml v2.0.53）。

旧默认落盘值 50_000 改写为 0（移交前线游戏侧承接静默暖场）且写回落盘推进版本至 2.0.53；
第二次加载全文逐字节零变更（幂等）；用户显式自定义值原样保留。
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from src.modules.config.multi_file_loader import generate_default_configs, get_config_version, load_config_dir
from src.modules.config.upgrade import _disable_game_lead_idle_fallback
from tests.config.helpers import tail_version


def _write_old_layout(tmp_path: Path, fallback_ms: int, version: str = "2.0.52") -> Path:
    """把 agents.toml 拨回旧布局：冷场兜底时长落盘为给定值，版本为 2.0.52。"""
    path = tmp_path / "agents.toml"
    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    doc["meta"]["version"] = version
    doc["agents"]["streamer"]["game_lead_idle_fallback_ms"] = fallback_ms
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return path


def test_old_default_rewritten_and_written_back(tmp_path: Path) -> None:
    """旧默认 50_000 经完整加载管线改写为 0、版本推进至 2.0.53；二次加载逐字节零变更。"""
    generate_default_configs(tmp_path)
    path = _write_old_layout(tmp_path, 50_000)

    load_config_dir(tmp_path)

    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    assert doc["agents"]["streamer"]["game_lead_idle_fallback_ms"] == 0
    assert doc["agents"]["streamer"]["game_lead_silence_nudge_ms"] == 75_000
    assert doc["meta"]["version"] == "2.0.53"
    assert get_config_version(tmp_path, "agents.toml") == tail_version("agents.toml")

    first_bytes = path.read_text(encoding="utf-8-sig")
    load_config_dir(tmp_path)
    assert path.read_text(encoding="utf-8-sig") == first_bytes


def test_user_custom_value_untouched() -> None:
    """用户显式自定义的值（非旧默认 50_000）原样保留，钩子返回空变更列表。"""
    data = {"agents": {"streamer": {"game_lead_idle_fallback_ms": 65_000}}}
    assert _disable_game_lead_idle_fallback(data) == []
    assert data["agents"]["streamer"]["game_lead_idle_fallback_ms"] == 65_000
    assert _disable_game_lead_idle_fallback({"agents": {"streamer": {}}}) == []


def test_user_custom_value_preserved_through_full_load(tmp_path: Path) -> None:
    """存量文件中用户显式自定义值走完整加载管线时版本推进到 2.0.53 且自定义值不被覆盖。"""
    generate_default_configs(tmp_path)
    path = _write_old_layout(tmp_path, 120_000)

    load_config_dir(tmp_path)

    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    assert doc["agents"]["streamer"]["game_lead_idle_fallback_ms"] == 120_000
    assert doc["meta"]["version"] == "2.0.53"
    assert get_config_version(tmp_path, "agents.toml") == "2.0.53"
