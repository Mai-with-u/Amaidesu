"""已删除字段在旧用户文件里的处置：冗余键全域硬错，加载期当面拦下（不静默剥离），保留字段的用户值不被覆盖

- [agents.streamer.context] 的 memory_recall_viewers / cache_ttl_ms（无召回后端载体；纯函数组装器下无正确缓存语义）
- [agents.streamer] 的死开关 enable_action_selection（Replyer 从不读取）
"""

from __future__ import annotations

import re

import pytest

from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir


@pytest.fixture
def agents_path(tmp_path):
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    return config_dir / "agents.toml"


@pytest.mark.parametrize(
    ("anchor", "injected", "removed"),
    [
        (
            "[agents.streamer.context]",
            "memory_recall_viewers = 5\ncache_ttl_ms = 2000",
            ("memory_recall_viewers", "cache_ttl_ms"),
        ),
        ("[agents.streamer]", "enable_action_selection = true", ("enable_action_selection",)),
    ],
    ids=["context-fields", "action-selection-switch"],
)
def test_removed_fields_hard_fail_naming_the_key_and_the_migration_hook(agents_path, anchor, injected, removed) -> None:
    content = agents_path.read_text(encoding="utf-8-sig")
    assert anchor in content
    agents_path.write_text(content.replace(anchor, f"{anchor}\n{injected}", 1), encoding="utf-8-sig")

    with pytest.raises(ConfigValidationError) as exc_info:
        load_config_dir(agents_path.parent)
    message = str(exc_info.value)
    assert all(key in message for key in removed) and "迁移钩子" in message


def test_user_values_of_kept_context_fields_are_preserved(agents_path) -> None:
    content = agents_path.read_text(encoding="utf-8-sig")
    # [agents.streamer.context] 段的 enabled 改为 false（段内还有历史预算字段与注释行）
    changed = re.sub(r"(\[agents\.streamer\.context\]\n(?:(?:#|\w+ = )[^\n]*\n)*?enabled = )true", r"\1false", content)
    assert changed != content
    agents_path.write_text(changed, encoding="utf-8-sig")

    config, _ = load_config_dir(agents_path.parent)
    assert config["agents"]["agents"]["streamer"]["context"]["enabled"] is False
