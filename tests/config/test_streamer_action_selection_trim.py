"""[agents.streamer] 死开关 enable_action_selection 的漂移写回剥离测试。

字段已从 StreamerConfig 删除（Replyer 从不读取），旧用户文件中的残留键
由 Pydantic 漂移写回自动清理（先例：test_context_assembler_trim.py）。
"""

from __future__ import annotations

import shutil

import pytest

from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir


@pytest.fixture
def config_dir(tmp_path):
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    yield config_dir
    if config_dir.exists():
        shutil.rmtree(config_dir)


class TestEnableActionSelectionTrim:
    """旧文件的 enable_action_selection 键 → 加载不炸、写回被剥离。"""

    def test_removed_field_stripped_on_writeback(self, config_dir):
        agents_path = config_dir / "agents.toml"
        content = agents_path.read_text(encoding="utf-8-sig")
        assert "[agents.streamer]" in content
        # 模拟旧用户配置：根段残留已删除的死开关
        new_content = content.replace(
            "[agents.streamer]",
            "[agents.streamer]\nenable_action_selection = true",
            1,
        )
        agents_path.write_text(new_content, encoding="utf-8-sig")

        # 加载不炸；合并视图中字段被剥除
        config, _ = load_config_dir(config_dir)
        streamer_cfg = config["agents"]["agents"]["streamer"]
        assert "enable_action_selection" not in streamer_cfg
        assert "history_limit" in streamer_cfg

        # 写回落盘后文件中不再含该键
        written = agents_path.read_text(encoding="utf-8-sig")
        assert "enable_action_selection" not in written
