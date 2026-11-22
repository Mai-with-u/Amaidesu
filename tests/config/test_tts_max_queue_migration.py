"""TTS 发声队列默认容量 3 → 6 的迁移测试（infra.toml v2.0.46）。

旧默认落盘值 3 改写为 6（容纳长弹幕分段播报叠加欢迎/实况解说，避免轻易触发 queue_overflow）
且写回落盘推进版本至 2.0.46；第二次加载全文逐字节零变更（幂等）；用户显式自定义值原样保留。
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from src.modules.config.multi_file_loader import generate_default_configs, get_config_version, load_config_dir
from src.modules.config.upgrade import _widen_tts_max_queue
from tests.config.helpers import tail_version


def _write_old_layout(tmp_path: Path, max_queue: int, version: str = "2.0.45") -> Path:
    """把 infra.toml 拨回旧布局：tts.max_queue 落盘为给定值，版本为 2.0.45。"""
    path = tmp_path / "infra.toml"
    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    doc["meta"]["version"] = version
    doc["tts"]["max_queue"] = max_queue
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return path


def test_old_default_rewritten_and_written_back(tmp_path: Path) -> None:
    """旧默认 3 经完整加载管线改写为 6、版本推进至 2.0.46；二次加载逐字节零变更。"""
    generate_default_configs(tmp_path)
    path = _write_old_layout(tmp_path, 3)

    load_config_dir(tmp_path)

    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    assert doc["tts"]["max_queue"] == 6
    assert doc["meta"]["version"] == "2.0.46"
    assert get_config_version(tmp_path, "infra.toml") == tail_version("infra.toml")

    first_bytes = path.read_text(encoding="utf-8-sig")
    load_config_dir(tmp_path)
    assert path.read_text(encoding="utf-8-sig") == first_bytes


def test_user_custom_value_untouched() -> None:
    """用户显式自定义的值（非旧默认 3）原样保留，钩子返回空变更列表。"""
    data = {"tts": {"max_queue": 10}}
    assert _widen_tts_max_queue(data) == []
    assert data["tts"]["max_queue"] == 10
    assert _widen_tts_max_queue({"tts": {}}) == []


def test_user_custom_value_preserved_through_full_load(tmp_path: Path) -> None:
    """存量文件中用户显式自定义值走完整加载管线时版本推进到 2.0.46 且自定义值不被覆盖。"""
    generate_default_configs(tmp_path)
    path = _write_old_layout(tmp_path, 8)

    load_config_dir(tmp_path)

    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    assert doc["tts"]["max_queue"] == 8
    assert doc["meta"]["version"] == "2.0.46"
    assert get_config_version(tmp_path, "infra.toml") == "2.0.46"
