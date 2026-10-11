"""[agents.streamer] 死开关 enable_action_selection 的全域硬错测试。

字段已从 StreamerConfig 删除（Replyer 从不读取），旧用户文件中的残留键
在加载期当面拦下（冗余键全域硬错，不再静默剥离；先例：test_context_assembler_trim.py）。
"""

from __future__ import annotations

import shutil

import pytest

from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir


@pytest.fixture
def config_dir(tmp_path):
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    yield config_dir
    if config_dir.exists():
        shutil.rmtree(config_dir)


class TestEnableActionSelectionTrim:
    """旧文件的 enable_action_selection 键 → 加载硬错、消息点名。"""

    def test_removed_field_hard_fails(self, config_dir):
        agents_path = config_dir / "agents.toml"
        content = agents_path.read_text(encoding="utf-8-sig")
        assert "[agents.streamer]" in content
        new_content = content.replace(
            "[agents.streamer]",
            "[agents.streamer]\nenable_action_selection = true",
            1,
        )
        agents_path.write_text(new_content, encoding="utf-8-sig")

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config_dir(config_dir)
        message = str(exc_info.value)
        assert "enable_action_selection" in message
        assert "迁移钩子" in message
