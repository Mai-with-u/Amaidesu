"""[agents.streamer.context] 组装器字段收口测试。

StreamerContextConfig 删除 ``memory_recall_viewers`` / ``cache_ttl_ms``
（前者无召回后端载体——SimpleMemory 无画像批量召回接口；后者在纯函数
组装器 + 每批动态窗口下无正确缓存语义），漂移写回自动清理用户文件中的
冗余键。context 段已从顶层 [context] 迁入 [agents.streamer.context]。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from src.modules.config.file_meta import CONFIG_BASELINE_VERSION
from src.modules.config.multi_file_loader import (
    generate_default_configs,
    load_config_dir,
)


@pytest.fixture
def config_dir(tmp_path):
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    yield config_dir
    if config_dir.exists():
        shutil.rmtree(config_dir)


class TestContextAssemblerFieldTrim:
    """[agents.streamer.context] 冗余字段（viewers/cache_ttl_ms）写回清理。"""

    def test_removed_fields_stripped_on_writeback(self, config_dir):
        """旧文件的 [agents.streamer.context] 四键 → 加载后冗余键清理、保留两键、版本保持。"""
        agents_path = config_dir / "agents.toml"
        content = agents_path.read_text(encoding="utf-8-sig")
        assert "[agents.streamer.context]" in content
        new_content = content.replace(
            "[agents.streamer.context]",
            '[agents.streamer.context]\nmemory_recall_viewers = 5\ncache_ttl_ms = 2000',
            1,
        )
        agents_path.write_text(new_content, encoding="utf-8-sig")

        config, _ = load_config_dir(config_dir)

        context_cfg = config["agents"]["agents"]["streamer"]["context"]
        assert "memory_recall_viewers" not in context_cfg
        assert "cache_ttl_ms" not in context_cfg
        assert "enabled" in context_cfg
        assert "memory_recall_long_term" in context_cfg
        assert context_cfg["enabled"] is True

        written = agents_path.read_text(encoding="utf-8-sig")
        assert f'version = "{CONFIG_BASELINE_VERSION}"' in written

    def test_user_values_of_kept_fields_preserved(self, config_dir):
        """保留字段的用户值不被默认值覆盖。"""
        agents_path = config_dir / "agents.toml"
        content = agents_path.read_text(encoding="utf-8-sig")
        # [agents.streamer.context] 段的 enabled 改为 false；召回条数改 7
        new_content = re.sub(
            r"(\[agents\.streamer\.context\]\n(?:#[^\n]*\n)*enabled = )true",
            r"\1false",
            content,
        )
        assert new_content != content
        new_content = new_content.replace("memory_recall_long_term = 3", "memory_recall_long_term = 7", 1)
        agents_path.write_text(new_content, encoding="utf-8-sig")

        config, _ = load_config_dir(config_dir)

        context_cfg = config["agents"]["agents"]["streamer"]["context"]
        assert context_cfg["enabled"] is False
        assert context_cfg["memory_recall_long_term"] == 7
