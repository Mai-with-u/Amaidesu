"""直播画面挂帧节流键退役迁移测试（agents.toml v2.0.46）。

决策窗改为每窗必挂暂存最新帧，``[agents.streamer.live_frame]`` 的
``max_age_ms`` / ``min_interval_ms`` 失去消费者，加载期原地删除；
其他段的 ``min_interval_ms`` 不受影响；重复加载零变更。
"""

from pathlib import Path

import tomlkit

from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.config.upgrade import _retire_live_frame_gates


def _write_old_live_frame(tmp_path: Path, version: str = "2.0.45") -> Path:
    """把 agents.toml 拨回旧布局：live_frame 段带两道节流键，版本 2.0.45。"""
    path = tmp_path / "agents.toml"
    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    doc["meta"]["version"] = version
    live_frame = doc["agents"]["streamer"]["live_frame"]
    live_frame["max_age_ms"] = 15_000
    live_frame["min_interval_ms"] = 15_000
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return path


def test_live_frame_gates_removed_and_written_back(tmp_path: Path) -> None:
    """旧键删除、stale_warn_ms 按新默认物化落盘，版本推进到 2.0.47；再次加载零变更。"""
    generate_default_configs(tmp_path)
    path = _write_old_live_frame(tmp_path)

    load_config_dir(tmp_path)

    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    live_frame = doc["agents"]["streamer"]["live_frame"]
    assert "max_age_ms" not in live_frame and "min_interval_ms" not in live_frame
    assert live_frame["stale_warn_ms"] == 60_000
    assert doc["meta"]["version"] == "2.0.47"

    first = path.read_text(encoding="utf-8-sig")
    load_config_dir(tmp_path)
    assert path.read_text(encoding="utf-8-sig") == first


def test_other_min_interval_keys_are_untouched() -> None:
    """proactive 等其他段的 min_interval_ms 是活跃配置，迁移不许误伤。"""
    data = {
        "agents": {
            "streamer": {
                "live_frame": {"enabled": True, "max_age_ms": 15_000, "min_interval_ms": 15_000},
                "proactive": {"min_interval_ms": 120_000},
            }
        }
    }
    changed = _retire_live_frame_gates(data)
    streamer = data["agents"]["streamer"]
    assert changed == [
        "agents.streamer.live_frame.max_age_ms",
        "agents.streamer.live_frame.min_interval_ms",
    ]
    assert streamer["live_frame"] == {"enabled": True}
    assert streamer["proactive"]["min_interval_ms"] == 120_000
    assert _retire_live_frame_gates(data) == []
