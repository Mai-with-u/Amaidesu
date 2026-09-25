"""旧生成额度、时限与输入裁剪字段实际退役落盘，自动摘要配置完整保留。"""

from pathlib import Path
from typing import Any

import pytest
import tomlkit

from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir


def _section(data: Any, path: str) -> tuple[Any, str]:
    """定位配置段，提供商列表按下标访问，其余字段按键访问。"""
    parts = path.split(".")
    for part in parts[:-1]:
        data = data[int(part)] if part.isdigit() else data.setdefault(part, {})
    return data, parts[-1]


@pytest.mark.parametrize(
    ("filename", "removed"),
    [
        (
            "model.toml",
            (
                "llm_profiles.planner.max_tokens",
                "llm_profiles.planner.hard_timeout_ms",
                "llm_providers.0.timeout",
                "llm_providers.0.max_tokens",
            ),
        ),
        (
            "agents.toml",
            (
                "agents.streamer.history_limit",
                "agents.minecraft.builder.max_resource_chars",
                "agents.minecraft.context.observation_inline_chars",
                "agents.minecraft.context.archive_max_chars",
            ),
        ),
        ("infra.toml", ("simulator.context_window_size", "simulator.max_message_chars")),
        (
            "tools.toml",
            (
                "tools.vision.config.vlm_timeout_ms",
                "tools.vision.config.default_max_width",
                "tools.web.search.config.max_fetch_chars",
            ),
        ),
        ("storage.toml", ("memory.profile_injection_max",)),
    ],
)
def test_legacy_limits_removed_from_disk_only_for_affected_file(
    tmp_path: Path, filename: str, removed: tuple[str, ...]
) -> None:
    """自定义旧上限被删除；无关文件不动，重载不重复写回。"""
    generate_default_configs(tmp_path)
    load_config_dir(tmp_path)
    target = tmp_path / filename
    data = tomlkit.parse(target.read_text(encoding="utf-8-sig"))
    data["meta"]["version"] = "2.0.39"
    for path in removed:
        section, key = _section(data, path)
        section[key] = 12345
    target.write_text(tomlkit.dumps(data), encoding="utf-8")
    others = {path: path.read_bytes() for path in tmp_path.glob("*.toml") if path != target}

    load_config_dir(tmp_path)

    migrated = tomlkit.parse(target.read_text(encoding="utf-8-sig"))
    assert migrated["meta"]["version"] == "2.0.40"
    for path in removed:
        section, key = _section(migrated, path)
        assert key not in section
    assert all(path.read_bytes() == content for path, content in others.items())
    written = target.read_bytes()
    _, report = load_config_dir(tmp_path)
    assert not report.has_drift and target.read_bytes() == written


def test_summary_thresholds_and_custom_values_survive_upgrade(tmp_path: Path) -> None:
    """此次清理保留父玩家和建造设计的摘要阈值、近期轮数及摘要长度。"""
    generate_default_configs(tmp_path)
    path = tmp_path / "agents.toml"
    data = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    data["meta"]["version"] = "2.0.39"
    minecraft = data["agents"]["minecraft"]
    for section in (minecraft["context"], minecraft["builder"]):
        section["max_context_chars"] = 234000
        section["summary_max_chars"] = 7000
        section["recent_turns"] = 8
    path.write_text(tomlkit.dumps(data), encoding="utf-8")
    load_config_dir(tmp_path)
    migrated = tomlkit.parse(path.read_text(encoding="utf-8-sig"))["agents"]["minecraft"]
    for section in (migrated["context"], migrated["builder"]):
        assert section["max_context_chars"] == 234000
        assert section["summary_max_chars"] == 7000
        assert section["recent_turns"] == 8
