"""Minecraft 两个采集器搬出 collectors.toml 的迁移测试（collectors.toml v2.0.43）

身体事件流与聊天流改为随 Minecraft Agent 开关的附属采集器，配置由 [agents.minecraft] 换算。
覆盖：
- 旧布局（enabled 里有这两个名字、文件里有这两段）→ 加载 → 名字与段都被搬走、版本推进、写回磁盘；
- agents.toml 不被这次迁移改动（各文件版本流独立）；
- 二次加载零写回（幂等）。
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir


def _write_old_collectors_layout(config_dir: Path) -> None:
    """把 collectors.toml 拨回旧布局：两个 Minecraft 采集器在名单里、各有一段，版本 2.0.40。"""
    path = config_dir / "collectors.toml"
    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    doc["meta"]["version"] = "2.0.40"
    doc["enabled"] = ["console_input", "maicraft_attention", "maicraft_chat"]
    doc["maicraft_attention"] = {
        "url": "http://127.0.0.1:8766/mcp",
        "transport": "http",
        "retry_interval_ms": 5000,
        "wait_ms": 25000,
    }
    doc["maicraft_chat"] = {"url": "http://192.168.1.9:8766/mcp", "retry_interval_ms": 9000}
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")


def test_companion_sections_moved_out_and_written_back(tmp_path: Path) -> None:
    generate_default_configs(tmp_path)
    # 新生成的各文件先加载一次走完各自的版本链，之后的比较只看这次迁移
    load_config_dir(tmp_path)
    agents_before = (tmp_path / "agents.toml").read_text(encoding="utf-8-sig")
    _write_old_collectors_layout(tmp_path)

    load_config_dir(tmp_path)

    doc = tomlkit.parse((tmp_path / "collectors.toml").read_text(encoding="utf-8-sig"))
    assert list(doc["enabled"]) == ["console_input"]
    assert "maicraft_attention" not in doc
    assert "maicraft_chat" not in doc
    assert doc["meta"]["version"] == "2.0.43"
    # 只动 collectors.toml：agents.toml 的内容与版本都不随这次迁移变化
    assert (tmp_path / "agents.toml").read_text(encoding="utf-8-sig") == agents_before


def test_companion_migration_idempotent(tmp_path: Path) -> None:
    generate_default_configs(tmp_path)
    _write_old_collectors_layout(tmp_path)

    load_config_dir(tmp_path)
    first = (tmp_path / "collectors.toml").read_text(encoding="utf-8-sig")

    load_config_dir(tmp_path)
    second = (tmp_path / "collectors.toml").read_text(encoding="utf-8-sig")

    assert first == second
