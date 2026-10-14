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
    ("filename", "removed", "final_version"),
    [
        (
            "model.toml",
            (
                "llm_profiles.planner.max_tokens",
                "llm_profiles.planner.hard_timeout_ms",
                "llm_providers.0.timeout",
                "llm_providers.0.max_tokens",
            ),
            # model.toml 钩子链最长（cache 开关键退役 v2.0.43）
            "2.0.43",
        ),
        (
            "agents.toml",
            (
                "agents.streamer.history_limit",
                "agents.minecraft.context.observation_inline_chars",
                "agents.minecraft.context.archive_max_chars",
            ),
            # agents.toml 钩子链延到 Minecraft 上下文预算改 token（v2.0.44）
            "2.0.44",
        ),
        # infra.toml 的钩子链更长（sc_probability 死键清理 v2.0.41）
        ("infra.toml", ("simulator.context_window_size", "simulator.max_message_chars"), "2.0.42"),
        (
            "tools.toml",
            (
                "tools.vision.config.vlm_timeout_ms",
                "tools.web.search.config.max_fetch_chars",
            ),
            "2.0.40",
        ),
        ("storage.toml", ("memory.profile_injection_max",), "2.0.40"),
    ],
)
def test_legacy_limits_removed_from_disk_only_for_affected_file(
    tmp_path: Path, filename: str, removed: tuple[str, ...], final_version: str
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
    assert migrated["meta"]["version"] == final_version
    for path in removed:
        section, key = _section(migrated, path)
        assert key not in section
    assert all(path.read_bytes() == content for path, content in others.items())
    written = target.read_bytes()
    _, report = load_config_dir(tmp_path)
    assert not report.has_drift and target.read_bytes() == written


def test_revived_default_max_width_materializes_after_removal_hook(tmp_path: Path) -> None:
    """default_max_width 以新语义复活（进对话图像的传输降采样默认）。

    2.0.40 钩子按退役处置移除旧值（旧值属 VLM 转述时代语义，不保留），
    物化器随后按注册表补回新默认 1280；vlm_timeout_ms 保持退役不回写。
    """
    generate_default_configs(tmp_path)
    load_config_dir(tmp_path)
    target = tmp_path / "tools.toml"
    data = tomlkit.parse(target.read_text(encoding="utf-8-sig"))
    data["meta"]["version"] = "2.0.39"
    section = data["tools"]["vision"]["config"]
    section["vlm_timeout_ms"] = 12345
    section["default_max_width"] = 800
    target.write_text(tomlkit.dumps(data), encoding="utf-8")

    load_config_dir(tmp_path)

    migrated = tomlkit.parse(target.read_text(encoding="utf-8-sig"))["tools"]["vision"]["config"]
    assert "vlm_timeout_ms" not in migrated
    assert migrated["default_max_width"] == 1280


def test_summary_thresholds_and_custom_values_survive_upgrade(tmp_path: Path) -> None:
    """此次清理保留玩家的摘要阈值、近期轮数及摘要长度；字符预算随后换算成 token 预算。"""
    generate_default_configs(tmp_path)
    path = tmp_path / "agents.toml"
    data = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    data["meta"]["version"] = "2.0.39"
    minecraft = data["agents"]["minecraft"]
    for section in (minecraft["context"],):
        # 旧布局只有字符预算，没有 token 预算字段
        section.pop("max_tokens", None)
        section["max_context_chars"] = 234000
        section["summary_max_chars"] = 7000
        section["recent_turns"] = 8
    path.write_text(tomlkit.dumps(data), encoding="utf-8")
    load_config_dir(tmp_path)
    migrated = tomlkit.parse(path.read_text(encoding="utf-8-sig"))["agents"]["minecraft"]
    for section in (migrated["context"],):
        assert "max_context_chars" not in section and section["max_tokens"] == 78000
        assert section["summary_max_chars"] == 7000
        assert section["recent_turns"] == 8
