"""每文件版本推进调度 + 升级钩子注册表

调度规则：钩子条件 old < target；推进到该文件最后一个已执行钩子的 target；无适用钩子的文件不推进；
跨文件钩子双写，目标文件推进到钩子 target 且不回退；版本缺失硬错。演示钩子只在本文件定义，不注册进生产注册表。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.modules.config import upgrade
from src.modules.config.errors import ConfigValidationError
from src.modules.config.file_meta import CONFIG_BASELINE_VERSION
from src.modules.config.multi_file_loader import generate_default_configs, get_config_version, load_config_dir
from src.modules.config.upgrade import advance_file_versions
from tests.config.helpers import rewind_and_append, tail_version


def sample_hook(data: Dict[str, Any]) -> List[str]:
    """样例：旧键 [agents].bot_name 搬入合法新位 [agents.streamer.persona]（幂等：旧键已迁移则零变更）。"""
    agents = data.get("agents") or {}
    old_name = agents.pop("bot_name", None)
    if old_name is None:
        return []
    data["agents"].setdefault("streamer", {}).setdefault("persona", {}).setdefault("bot_name", old_name)
    return ["agents.bot_name -> agents.streamer.persona.bot_name"]


def sample_cross_hook(host_data: Dict[str, Any], target_data: Dict[str, Any]) -> List[str]:
    """样例跨文件钩子：把宿主标记搬运到目标文件段。"""
    moved = host_data.get("agents", {}).pop("sample_migrated", None)
    if moved is None:
        return []
    target_data.setdefault("tools", {})["sample_received"] = moved
    return ["agents.sample_migrated", "tools.sample_received"]


@pytest.fixture(autouse=True)
def isolated_hooks():
    """隔离生产钩子：暂存清空 agents/tools 的生产钩子链，测试后恢复，防止注册表污染泄漏到其他测试。"""
    saved = {name: upgrade._FILE_HOOKS.pop(name, None) for name in ("agents.toml", "tools.toml")}
    yield
    for name, hooks in saved.items():
        upgrade._FILE_HOOKS.pop(name, None)
        if hooks is not None:
            upgrade._FILE_HOOKS[name] = hooks


def _raw(version: str, **sections: Any) -> Dict[str, Any]:
    return {"meta": {"version": version}, **sections}


def test_advances_to_the_last_executed_hook_target_not_the_baseline() -> None:
    raw = {"agents.toml": _raw("2.0.30", agents={"bot_name": "麦麦"})}
    upgrade.register_file_hook("agents.toml", "sample", "2.0.31", sample_hook)
    changed = advance_file_versions(raw)
    assert raw["agents.toml"]["meta"]["version"] == "2.0.31"
    assert raw["agents.toml"]["agents"]["streamer"]["persona"]["bot_name"] == "麦麦"
    assert changed["agents.toml"] == ["agents.bot_name -> agents.streamer.persona.bot_name", "meta.version"]

    ahead = {"agents.toml": _raw("2.0.32", agents={})}  # 钩子 target 高于历史基线时同样推进，调度不含基线上界
    upgrade._FILE_HOOKS.pop("agents.toml")
    upgrade.register_file_hook("agents.toml", "future", "2.0.33", lambda data: [])
    assert (
        advance_file_versions(ahead)["agents.toml"] == ["meta.version"]
        and ahead["agents.toml"]["meta"]["version"] == "2.0.33"
    )


def test_files_without_applicable_hooks_or_already_ahead_are_untouched() -> None:
    upgrade.register_file_hook("agents.toml", "sample", "2.0.31", sample_hook)
    raw = {"agents.toml": _raw("2.0.30", agents={"bot_name": "麦麦"}), "tools.toml": _raw("2.0.30", tools={})}
    changed = advance_file_versions(raw)
    assert raw["tools.toml"]["meta"]["version"] == "2.0.30" and set(changed) == {"agents.toml"}  # 无钩子文件保持原版本

    ahead = {"agents.toml": _raw("9.9.9", agents={"bot_name": "麦麦"})}
    assert (
        advance_file_versions(ahead) == {} and "bot_name" in ahead["agents.toml"]["agents"]
    )  # 版本高于全部钩子 target：零变更


def test_rerun_is_a_noop_and_hooks_are_idempotent() -> None:
    upgrade.register_file_hook("agents.toml", "sample", "2.0.31", sample_hook)
    raw = {"agents.toml": _raw("2.0.30", agents={"bot_name": "麦麦"})}
    advance_file_versions(raw)
    assert (
        advance_file_versions(raw) == {} and raw["agents.toml"]["meta"]["version"] == "2.0.31"
    )  # 第二次 old == target，零变更

    raw["agents.toml"]["meta"]["version"] = "2.0.30"  # 模拟重放：版本拨回旧值再跑，数据已含迁移标记
    assert advance_file_versions(raw)["agents.toml"] == ["meta.version"]


def test_missing_version_is_a_hard_error() -> None:
    with pytest.raises(ConfigValidationError, match="version"):
        advance_file_versions({"agents.toml": {"agents": {}}})


@pytest.mark.parametrize(
    ("target_version", "expected_version", "target_changed"),
    [
        ("2.0.30", "2.0.31", ["meta.version"]),
        ("2.0.31", "2.0.31", []),
    ],  # 目标已高于/等于钩子 target 时不回退，数据改写仍标记写回
    ids=["target-advanced", "target-already-at-hook-target"],
)
def test_cross_file_hook_writes_both_files(target_version: str, expected_version: str, target_changed: list) -> None:
    upgrade.register_cross_file_hook("agents.toml", "tools.toml", "mover", "2.0.31", sample_cross_hook)
    raw = {
        "agents.toml": _raw("2.0.30", agents={"sample_migrated": True}),
        "tools.toml": _raw(target_version, tools={}),
    }
    changed = advance_file_versions(raw)

    assert "sample_migrated" not in raw["agents.toml"]["agents"] and raw["agents.toml"]["meta"]["version"] == "2.0.31"
    assert raw["tools.toml"]["tools"]["sample_received"] is True  # 目标收到数据
    assert raw["tools.toml"]["meta"]["version"] == expected_version
    assert changed["tools.toml"] == target_changed


# 加载管线闭环（真实文件）


def test_generated_files_start_at_baseline_and_missing_version_fails_load(tmp_path: Path) -> None:
    generate_default_configs(tmp_path)
    for name in ("agents.toml", "collectors.toml", "tools.toml", "model.toml", "storage.toml", "infra.toml"):
        assert get_config_version(tmp_path, name) == CONFIG_BASELINE_VERSION

    path = tmp_path / "tools.toml"
    path.write_text(
        path.read_text(encoding="utf-8-sig").replace(f'version = "{CONFIG_BASELINE_VERSION}"', "", 1),
        encoding="utf-8-sig",
    )
    with pytest.raises(ConfigValidationError, match="version"):
        load_config_dir(tmp_path)


def test_load_advances_only_files_with_hooks_writes_back_and_second_load_is_clean(tmp_path: Path) -> None:
    generate_default_configs(tmp_path)
    # 旧版布局：[agents] 段顶层带 bot_name（新版归属 streamer.persona）；tools 同样拨回旧版本作无钩子对照
    agents = (tmp_path / "agents.toml").read_text(encoding="utf-8-sig")
    (tmp_path / "agents.toml").write_text(
        agents.replace("[agents]\n", '[agents]\nbot_name = "麦麦"\n', 1), encoding="utf-8-sig"
    )
    rewind_and_append(tmp_path, "agents.toml", "2.0.30")
    rewind_and_append(tmp_path, "tools.toml", "2.0.30")
    upgrade.register_file_hook("agents.toml", "sample", "2.0.31", sample_hook)

    config, _ = load_config_dir(tmp_path)

    assert get_config_version(tmp_path, "agents.toml") == "2.0.31"  # 有适用钩子：推进到钩子 target 并写回
    assert config["agents"]["agents"]["streamer"]["persona"]["bot_name"] == "麦麦"
    assert get_config_version(tmp_path, "tools.toml") == "2.0.30"  # 无适用钩子：保持原值，不齐步走
    assert get_config_version(tmp_path, "infra.toml") == tail_version(
        "infra.toml"
    )  # 未拨版本但有生产钩子的文件随调度推进
    # 钩子 target 都不高于基线的文件：没有适用钩子，保持基线种子值
    assert get_config_version(tmp_path, "storage.toml") == CONFIG_BASELINE_VERSION

    _, report = load_config_dir(tmp_path)  # 二次加载：各文件 old == 当前值，零写回
    assert not report.has_drift
    assert (
        get_config_version(tmp_path, "agents.toml") == "2.0.31"
        and get_config_version(tmp_path, "tools.toml") == "2.0.30"
    )
