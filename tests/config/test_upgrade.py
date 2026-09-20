"""每文件版本推进 + 升级钩子注册表测试

覆盖：
- 版本推进调度：钩子条件 ``old < target``、推进到"作用于该文件的最后一个
  已执行钩子的 target_version"、无适用钩子的文件不推进、幂等、变更路径返回
- 版本缺失硬错；跨文件钩子双写 + 目标文件推进到该钩子 target
- 全新生成 6 文件版本独立（改单文件不带动他文件）

演示样例钩子只在本文件定义，不注册进生产注册表。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.modules.config.file_meta import CONFIG_BASELINE_VERSION
from src.modules.config.multi_file_loader import (
    generate_default_configs,
    get_config_version,
    load_config_dir,
)
from src.modules.config import upgrade
from src.modules.config.errors import ConfigValidationError
from src.modules.config.upgrade import advance_file_versions


# ---------------------------------------------------------------------------
# 演示样例钩子（自测用，不注册生效）
# ---------------------------------------------------------------------------


def sample_hook_v2_0_31(data: Dict[str, Any]) -> List[str]:
    """样例：旧键 [agents].bot_name 搬入新位 [agents.streamer.persona]（幂等）。

    搬入目标为 Schema 合法字段路径——真实迁移钩子的变更必须通过阶段④校验。
    """
    agents = data.get("agents") or {}
    old_name = agents.pop("bot_name", None)
    if old_name is None:
        return []  # 幂等：旧键已迁移则零变更
    persona = data["agents"].setdefault("streamer", {}).setdefault("persona", {})
    persona.setdefault("bot_name", old_name)
    return ["agents.bot_name -> agents.streamer.persona.bot_name"]


def sample_cross_hook(host_data: Dict[str, Any], target_data: Dict[str, Any]) -> List[str]:
    """样例跨文件钩子：把宿主标记搬运到目标文件段（模拟段搬移）。"""
    agents = host_data.get("agents", {})
    moved = agents.pop("sample_migrated", None)
    if moved is None:
        return []
    tools = target_data.setdefault("tools", {})
    tools["sample_received"] = moved
    return ["agents.sample_migrated", "tools.sample_received"]


# ---------------------------------------------------------------------------
# 调度器单测
# ---------------------------------------------------------------------------


class TestAdvanceFileVersions:
    """调度器单测：与生产钩子注册表隔离（暂存清空，测试后恢复）。"""

    def setup_method(self) -> None:
        self._saved_agents_hooks = upgrade._FILE_HOOKS.pop("agents.toml", None)
        # tools.toml 的生产钩子一并隔离：本类把 tools.toml 当"无钩子对照文件"
        self._saved_tools_hooks = upgrade._FILE_HOOKS.pop("tools.toml", None)

    def teardown_method(self) -> None:
        upgrade._FILE_HOOKS.pop("agents.toml", None)
        if self._saved_agents_hooks is not None:
            upgrade._FILE_HOOKS["agents.toml"] = self._saved_agents_hooks
        upgrade._FILE_HOOKS.pop("tools.toml", None)
        if self._saved_tools_hooks is not None:
            upgrade._FILE_HOOKS["tools.toml"] = self._saved_tools_hooks

    def test_advances_to_last_executed_hook_target(self):
        """有适用钩子的文件推进到最后一个已执行钩子的 target（不是基线）"""
        raw = {"agents.toml": {"meta": {"version": "2.0.30"}, "agents": {"bot_name": "麦麦"}}}
        upgrade.register_file_hook("agents.toml", "sample", "2.0.31", sample_hook_v2_0_31)
        try:
            changed = advance_file_versions(raw)
        finally:
            upgrade._FILE_HOOKS.pop("agents.toml", None)

        assert raw["agents.toml"]["meta"]["version"] == "2.0.31"
        persona = raw["agents.toml"]["agents"]["streamer"]["persona"]
        assert persona["bot_name"] == "麦麦"
        assert changed["agents.toml"] == ["agents.bot_name -> agents.streamer.persona.bot_name", "meta.version"]

    def test_advances_past_old_baseline_when_hook_target_higher(self):
        """钩子 target 高于历史基线时同样推进（调度不含基线上界）"""
        raw = {"agents.toml": {"meta": {"version": "2.0.32"}, "agents": {}}}

        def noop(data: Dict[str, Any]) -> List[str]:
            return []

        upgrade.register_file_hook("agents.toml", "future", "2.0.33", noop)
        try:
            changed = advance_file_versions(raw)
        finally:
            upgrade._FILE_HOOKS.pop("agents.toml", None)

        assert raw["agents.toml"]["meta"]["version"] == "2.0.33"
        assert changed["agents.toml"] == ["meta.version"]

    def test_file_without_applicable_hooks_not_advanced(self):
        """无适用钩子的文件保持原版本，不进 changed、无版本戳变更"""
        raw = {
            "agents.toml": {"meta": {"version": "2.0.30"}, "agents": {"bot_name": "麦麦"}},
            "tools.toml": {"meta": {"version": "2.0.30"}, "tools": {}},
        }
        upgrade.register_file_hook("agents.toml", "sample", "2.0.31", sample_hook_v2_0_31)
        # 隔离 tools.toml 的生产钩子（本用例要求它是"无钩子文件"）
        saved_tools_hooks = upgrade._FILE_HOOKS.pop("tools.toml", None)
        try:
            changed = advance_file_versions(raw)
        finally:
            upgrade._FILE_HOOKS.pop("agents.toml", None)
            if saved_tools_hooks is not None:
                upgrade._FILE_HOOKS["tools.toml"] = saved_tools_hooks

        # 只有 A（有钩子）推进；B（无钩子）保持原值
        assert raw["agents.toml"]["meta"]["version"] == "2.0.31"
        assert raw["tools.toml"]["meta"]["version"] == "2.0.30"
        assert set(changed) == {"agents.toml"}

    def test_version_ahead_of_all_hooks_no_change(self):
        """版本高于全部钩子 target 的文件零变更"""
        raw = {"agents.toml": {"meta": {"version": "9.9.9"}, "agents": {"bot_name": "麦麦"}}}
        upgrade.register_file_hook("agents.toml", "sample", "2.0.31", sample_hook_v2_0_31)
        try:
            changed = advance_file_versions(raw)
        finally:
            upgrade._FILE_HOOKS.pop("agents.toml", None)

        assert changed == {}
        assert raw["agents.toml"]["meta"]["version"] == "9.9.9"
        assert "bot_name" in raw["agents.toml"]["agents"]

    def test_second_advance_is_noop(self):
        """连续两次推进：第二次 old == 钩子 target，零变更"""
        raw = {"agents.toml": {"meta": {"version": "2.0.30"}, "agents": {"bot_name": "麦麦"}}}
        upgrade.register_file_hook("agents.toml", "sample", "2.0.31", sample_hook_v2_0_31)
        try:
            advance_file_versions(raw)
            changed = advance_file_versions(raw)
        finally:
            upgrade._FILE_HOOKS.pop("agents.toml", None)

        assert changed == {}
        assert raw["agents.toml"]["meta"]["version"] == "2.0.31"

    def test_hook_idempotent_on_rerun(self):
        """同一数据重复推进：钩子幂等，第二次零变更"""
        raw = {"agents.toml": {"meta": {"version": "2.0.30"}, "agents": {}}}
        upgrade.register_file_hook("agents.toml", "sample", "2.0.31", sample_hook_v2_0_31)
        try:
            advance_file_versions(raw)
            # 模拟重放：把版本拨回旧值再跑（数据已含迁移标记）
            raw["agents.toml"]["meta"]["version"] = "2.0.30"
            changed = advance_file_versions(raw)
        finally:
            upgrade._FILE_HOOKS.pop("agents.toml", None)

        assert changed["agents.toml"] == ["meta.version"]

    def test_missing_version_raises(self):
        """存在文件缺 [meta].version → 硬错（ConfigValidationError）"""
        raw = {"agents.toml": {"agents": {}}}
        with pytest.raises(ConfigValidationError, match="version"):
            advance_file_versions(raw)

    def test_cross_file_hook_bumps_both_versions_to_hook_target(self):
        """跨文件钩子：双写 + 目标文件推进到该钩子 target（不是基线）"""
        raw = {
            "agents.toml": {"meta": {"version": "2.0.30"}, "agents": {"sample_migrated": True}},
            "tools.toml": {"meta": {"version": "2.0.30"}, "tools": {}},
        }
        upgrade.register_cross_file_hook("agents.toml", "tools.toml", "mover", "2.0.31", sample_cross_hook)
        # 隔离 tools.toml 的生产钩子（本用例要求目标文件版本只受演示钩子驱动）
        saved_tools_hooks = upgrade._FILE_HOOKS.pop("tools.toml", None)
        try:
            changed = advance_file_versions(raw)
        finally:
            upgrade._FILE_HOOKS.pop("agents.toml", None)
            if saved_tools_hooks is not None:
                upgrade._FILE_HOOKS["tools.toml"] = saved_tools_hooks

        # 宿主：标记搬走 + 版本推进到钩子 target
        assert "sample_migrated" not in raw["agents.toml"]["agents"]
        assert raw["agents.toml"]["meta"]["version"] == "2.0.31"
        # 目标：收到数据 + 版本推进到钩子 target
        assert raw["tools.toml"]["tools"]["sample_received"] is True
        assert raw["tools.toml"]["meta"]["version"] == "2.0.31"
        assert "tools.toml" in changed

    def test_cross_file_hook_target_not_downgraded(self):
        """目标文件版本已高于钩子 target 时不回退"""
        raw = {
            "agents.toml": {"meta": {"version": "2.0.30"}, "agents": {"sample_migrated": True}},
            "tools.toml": {"meta": {"version": "2.0.31"}, "tools": {}},
        }
        upgrade.register_cross_file_hook("agents.toml", "tools.toml", "mover", "2.0.31", sample_cross_hook)
        saved_tools_hooks = upgrade._FILE_HOOKS.pop("tools.toml", None)
        try:
            changed = advance_file_versions(raw)
        finally:
            upgrade._FILE_HOOKS.pop("agents.toml", None)
            if saved_tools_hooks is not None:
                upgrade._FILE_HOOKS["tools.toml"] = saved_tools_hooks

        assert raw["tools.toml"]["meta"]["version"] == "2.0.31"
        # 版本不回退；但钩子改写了目标数据 → 标记写回（无 meta.version 项）
        assert changed["tools.toml"] == []


# ---------------------------------------------------------------------------
# 加载管线闭环（真实文件）
# ---------------------------------------------------------------------------


class TestVersionPipeline:
    """加载管线闭环（真实文件）。

    体内会替换 agents.toml 的生产钩子链；setup/teardown 暂存恢复，
    防止注册表污染泄漏到其他测试（各文件版本流独立，恢复是硬要求）。
    """

    def setup_method(self) -> None:
        self._saved_agents_hooks = upgrade._FILE_HOOKS.pop("agents.toml", None)
        # tools.toml 的生产钩子一并隔离：本类把 tools.toml 当"无钩子对照文件"
        self._saved_tools_hooks = upgrade._FILE_HOOKS.pop("tools.toml", None)

    def teardown_method(self) -> None:
        upgrade._FILE_HOOKS.pop("agents.toml", None)
        if self._saved_agents_hooks is not None:
            upgrade._FILE_HOOKS["agents.toml"] = self._saved_agents_hooks
        upgrade._FILE_HOOKS.pop("tools.toml", None)
        if self._saved_tools_hooks is not None:
            upgrade._FILE_HOOKS["tools.toml"] = self._saved_tools_hooks

    def _write_version(self, config_dir: Path, file_name: str, version: str) -> None:
        path = config_dir / file_name
        content = path.read_text(encoding="utf-8-sig")
        content = content.replace(f'version = "{CONFIG_BASELINE_VERSION}"', f'version = "{version}"', 1)
        path.write_text(content, encoding="utf-8-sig")

    def test_generated_files_carry_baseline(self, tmp_path: Path):
        """全新生成 6 文件 → 版本全部为基线"""
        generate_default_configs(tmp_path)
        for fname in ("agents.toml", "collectors.toml", "tools.toml", "model.toml", "storage.toml", "infra.toml"):
            assert get_config_version(tmp_path, fname) == CONFIG_BASELINE_VERSION

    def test_version_advance_roundtrip(self, tmp_path: Path):
        """旧版本 + 已注册钩子 → load 推进到钩子 target 并写回（闭环）"""
        generate_default_configs(tmp_path)
        self._write_version(tmp_path, "agents.toml", "2.0.30")

        # 旧版布局：[agents] 段顶层带 bot_name（新版归属 streamer.persona）
        agents_path = tmp_path / "agents.toml"
        content = agents_path.read_text(encoding="utf-8-sig")
        content = content.replace("[agents]\n", '[agents]\nbot_name = "麦麦"\n', 1)
        agents_path.write_text(content, encoding="utf-8-sig")

        # 隔离生产钩子（agents.toml 的生产登记会改变推进目标）
        upgrade._FILE_HOOKS.pop("agents.toml", None)
        upgrade.register_file_hook("agents.toml", "sample", "2.0.31", sample_hook_v2_0_31)
        try:
            config, _report = load_config_dir(tmp_path)
        finally:
            upgrade._FILE_HOOKS.pop("agents.toml", None)

        assert get_config_version(tmp_path, "agents.toml") == "2.0.31"
        assert config["agents"]["agents"]["streamer"]["persona"]["bot_name"] == "麦麦"
        # 二次 load：old == 钩子 target，零变更零写盘
        _config2, report2 = load_config_dir(tmp_path)
        assert not report2.has_drift
        assert get_config_version(tmp_path, "agents.toml") == "2.0.31"

    def test_independent_advance(self, tmp_path: Path):
        """版本流独立：有钩子的文件推进到钩子 target，无钩子文件版本不动"""
        generate_default_configs(tmp_path)
        self._write_version(tmp_path, "agents.toml", "2.0.30")
        self._write_version(tmp_path, "tools.toml", "2.0.30")

        # 旧版布局标记，使 agents.toml 的样例钩子有实际变更
        agents_path = tmp_path / "agents.toml"
        content = agents_path.read_text(encoding="utf-8-sig")
        content = content.replace("[agents]\n", '[agents]\nbot_name = "麦麦"\n', 1)
        agents_path.write_text(content, encoding="utf-8-sig")

        # 隔离生产钩子（本测试断言推进目标 = 样例钩子 target 2.0.31）
        upgrade._FILE_HOOKS.pop("agents.toml", None)
        upgrade.register_file_hook("agents.toml", "sample", "2.0.31", sample_hook_v2_0_31)
        try:
            load_config_dir(tmp_path)
        finally:
            upgrade._FILE_HOOKS.pop("agents.toml", None)

        # A：有适用钩子 → 推进到钩子 target 并写回
        assert get_config_version(tmp_path, "agents.toml") == "2.0.31"
        # B：无适用钩子 → 保持原值，不齐步走
        assert get_config_version(tmp_path, "tools.toml") == "2.0.30"
        # 未拨版本的文件保持基线种子值
        assert get_config_version(tmp_path, "infra.toml") == CONFIG_BASELINE_VERSION

        # 二次 load：全部文件 old == 各自当前值，零写回
        _config2, report2 = load_config_dir(tmp_path)
        assert not report2.has_drift
        assert get_config_version(tmp_path, "tools.toml") == "2.0.30"

    def test_missing_version_hard_fail(self, tmp_path: Path):
        """删 [meta].version → load 硬错"""
        generate_default_configs(tmp_path)
        path = tmp_path / "tools.toml"
        content = path.read_text(encoding="utf-8-sig")
        content = content.replace(f'version = "{CONFIG_BASELINE_VERSION}"', "", 1)
        path.write_text(content, encoding="utf-8-sig")

        with pytest.raises(ConfigValidationError, match="version"):
            load_config_dir(tmp_path)
