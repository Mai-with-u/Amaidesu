"""配置 2.0.16 升级测试：[context] 组装器字段收口。

ContextAssemblerConfig 删除 ``memory_recall_viewers`` / ``cache_ttl_ms``
（前者无召回后端载体——SimpleMemory 无画像批量召回接口；后者在纯函数
组装器 + 每批动态窗口下无正确缓存语义），漂移写回自动清理用户文件中的
冗余键。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

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


def _set_version(config_dir: Path, version: str) -> None:
    core_path = config_dir / "core.toml"
    content = core_path.read_text(encoding="utf-8-sig")
    content = re.sub(r'version = "[^"]+"', f'version = "{version}"', content, count=1)
    core_path.write_text(content, encoding="utf-8-sig")


class TestContextAssemblerFieldTrim:
    """2.0.16：[context] 冗余字段（viewers/cache_ttl_ms）写回清理。"""

    def test_removed_fields_stripped_and_version_written_back(self, config_dir):
        """2.0.15 旧文件的 [context] 四键 → 加载后冗余键清理、保留两键、版本写回。"""
        core_path = config_dir / "core.toml"
        content = core_path.read_text(encoding="utf-8-sig")
        assert "[context]" in content
        new_content = content.replace(
            "[context]",
            '[context]\nmemory_recall_viewers = 5\ncache_ttl_ms = 2000',
            1,
        )
        core_path.write_text(new_content, encoding="utf-8-sig")
        _set_version(config_dir, "2.0.15")

        config, _ = load_config_dir(config_dir)

        context_cfg = config["core"]["context"]
        assert "memory_recall_viewers" not in context_cfg
        assert "cache_ttl_ms" not in context_cfg
        assert "enabled" in context_cfg
        assert "memory_recall_long_term" in context_cfg
        assert context_cfg["enabled"] is True

        written = core_path.read_text(encoding="utf-8-sig")
        assert 'version = "2.0.16"' in written

    def test_user_values_of_kept_fields_preserved(self, config_dir):
        """保留字段的用户值不被默认值覆盖。"""
        core_path = config_dir / "core.toml"
        content = core_path.read_text(encoding="utf-8-sig")
        # [context] 段的 enabled 改为 false；召回条数改 7
        new_content = re.sub(
            r"(\[context\]\n(?:#[^\n]*\n)*enabled = )true",
            r"\1false",
            content,
        )
        assert new_content != content
        new_content = new_content.replace("memory_recall_long_term = 3", "memory_recall_long_term = 7", 1)
        core_path.write_text(new_content, encoding="utf-8-sig")
        _set_version(config_dir, "2.0.15")

        config, _ = load_config_dir(config_dir)

        context_cfg = config["core"]["context"]
        assert context_cfg["enabled"] is False
        assert context_cfg["memory_recall_long_term"] == 7
