"""[agents.streamer.context] 组装器字段收口测试。

StreamerContextConfig 删除 ``memory_recall_viewers`` / ``cache_ttl_ms``
（前者无召回后端载体——SimpleMemory 无画像批量召回接口；后者在纯函数
组装器 + 每批动态窗口下无正确缓存语义）。冗余键全域硬错语义下，用户
文件中的已删字段在加载期当面拦下（而非静默剥离）。
context 段已从顶层 [context] 迁入 [agents.streamer.context]。
"""

from __future__ import annotations

import re
import shutil

import pytest

from src.modules.config.errors import ConfigValidationError
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
    """[agents.streamer.context] 已删字段 → 加载硬错（点名 + 出路提示）。"""

    def test_removed_fields_hard_fail(self, config_dir):
        """旧文件的已删字段 → ConfigValidationError，消息含字段名与钩子/修正指引。"""
        agents_path = config_dir / "agents.toml"
        content = agents_path.read_text(encoding="utf-8-sig")
        assert "[agents.streamer.context]" in content
        new_content = content.replace(
            "[agents.streamer.context]",
            '[agents.streamer.context]\nmemory_recall_viewers = 5\ncache_ttl_ms = 2000',
            1,
        )
        agents_path.write_text(new_content, encoding="utf-8-sig")

        with pytest.raises(ConfigValidationError) as exc_info:
            load_config_dir(config_dir)
        message = str(exc_info.value)
        assert "memory_recall_viewers" in message
        assert "cache_ttl_ms" in message
        assert "迁移钩子" in message

    def test_user_values_of_kept_fields_preserved(self, config_dir):
        """保留字段的用户值不被默认值覆盖。"""
        agents_path = config_dir / "agents.toml"
        content = agents_path.read_text(encoding="utf-8-sig")
        # [agents.streamer.context] 段的 enabled 改为 false
        new_content = re.sub(
            r"(\[agents\.streamer\.context\]\n(?:#[^\n]*\n)*enabled = )true",
            r"\1false",
            content,
        )
        assert new_content != content
        agents_path.write_text(new_content, encoding="utf-8-sig")

        config, _ = load_config_dir(config_dir)

        context_cfg = config["agents"]["agents"]["streamer"]["context"]
        assert context_cfg["enabled"] is False
