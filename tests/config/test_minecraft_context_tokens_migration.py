"""Minecraft 上下文预算由字符改为 token 的迁移测试（agents.toml v2.0.44）。

历届默认值（12 万 / 16 万字符）直接删除、由新默认接管；用户显式改过的值按约
3 字符/token 换算成 ``max_tokens``；已有 ``max_tokens`` 不覆盖；重复加载不再改写。
"""

from pathlib import Path

import pytest
import tomlkit

from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.config.upgrade import _upgrade_minecraft_context_tokens


def _write_old_context(tmp_path: Path, chars: int) -> Path:
    """把 agents.toml 拨回旧布局：Minecraft 上下文只有字符预算，版本 2.0.40。"""
    path = tmp_path / "agents.toml"
    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    doc["meta"]["version"] = "2.0.40"
    context = doc["agents"]["minecraft"]["context"]
    for key in ("window_ratio", "max_tokens", "prepare_ratio", "target_ratio"):
        context.pop(key, None)
    context["max_context_chars"] = chars
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return path


@pytest.mark.parametrize(("chars", "expected_tokens"), [(160_000, 200_000), (120_000, 200_000), (300_000, 100_000)])
def test_char_budget_converted_and_written_back(tmp_path: Path, chars: int, expected_tokens: int) -> None:
    """默认值换成新默认（20 万 token）；显式值按 3 字符/token 换算后落盘。"""
    generate_default_configs(tmp_path)
    path = _write_old_context(tmp_path, chars)

    load_config_dir(tmp_path)

    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    context = doc["agents"]["minecraft"]["context"]
    assert "max_context_chars" not in context
    assert context["max_tokens"] == expected_tokens
    assert doc["meta"]["version"] == "2.0.45"

    first = path.read_text(encoding="utf-8-sig")
    load_config_dir(tmp_path)
    assert path.read_text(encoding="utf-8-sig") == first


def test_existing_token_budget_is_not_overwritten() -> None:
    """同段已有 token 预算时只删旧字段，不用换算值覆盖用户已设的新值。"""
    data = {"agents": {"minecraft": {"context": {"max_context_chars": 600_000, "max_tokens": 50_000}}}}
    changed = _upgrade_minecraft_context_tokens(data)
    assert changed == ["agents.minecraft.context.max_context_chars"]
    assert data["agents"]["minecraft"]["context"] == {"max_tokens": 50_000}
    assert _upgrade_minecraft_context_tokens(data) == []
