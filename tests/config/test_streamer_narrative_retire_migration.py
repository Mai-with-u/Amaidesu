"""叙事缓冲配置段退役迁移测试（agents.toml v2.0.48）。

游戏事件素材改为统一追加式历史，三个叙事缓冲的保留期/条数上限配置失去
消费者，``[agents.streamer.narrative]`` 段加载期原地整段删除；其他段不受
影响；重复加载零变更。
"""

from pathlib import Path

import tomlkit

from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.config.upgrade import _retire_streamer_narrative


def _write_old_narrative(tmp_path: Path, version: str = "2.0.47") -> Path:
    """把 agents.toml 拨回旧布局：streamer 下带 narrative 段，版本 2.0.47。"""
    path = tmp_path / "agents.toml"
    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    doc["meta"]["version"] = version
    narrative = tomlkit.table()
    narrative["game_ttl_ms"] = 900_000
    narrative["body_max_items"] = 10
    doc["agents"]["streamer"]["narrative"] = narrative
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return path


def test_narrative_section_removed_and_written_back(tmp_path: Path) -> None:
    """整段删除落盘、版本推进到链上界；再次加载零变更（幂等）。"""
    generate_default_configs(tmp_path)
    path = _write_old_narrative(tmp_path)

    load_config_dir(tmp_path)

    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    assert "narrative" not in doc["agents"]["streamer"]
    assert doc["meta"]["version"] == "2.0.50"

    first = path.read_text(encoding="utf-8-sig")
    load_config_dir(tmp_path)
    assert path.read_text(encoding="utf-8-sig") == first


def test_missing_section_is_noop() -> None:
    """没有 narrative 段的配置零变更（幂等）。"""
    data: dict = {"agents": {"streamer": {"rundown_id": "demo"}}}
    assert _retire_streamer_narrative(data) == []
    assert data["agents"]["streamer"] == {"rundown_id": "demo"}
