"""每文件版本推进 + 升级钩子注册表测试

覆盖：
- 版本推进调度：区间语义（old < target <= baseline）、幂等、变更路径返回
- 版本缺失硬错；跨文件钩子双写 + 双版本同升
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
    def test_runs_interval_hooks_and_stamps_baseline(self):
        """区间 (old, baseline] 内的钩子执行，版本戳推进到基线"""
        raw = {"agents.toml": {"meta": {"version": "2.0.30"}, "agents": {"bot_name": "麦麦"}}}
        upgrade.register_file_hook("agents.toml", "sample", "2.0.31", sample_hook_v2_0_31)
        try:
            changed = advance_file_versions(raw)
        finally:
            upgrade._FILE_HOOKS.pop("agents.toml", None)

        assert raw["agents.toml"]["meta"]["version"] == CONFIG_BASELINE_VERSION
        persona = raw["agents.toml"]["agents"]["streamer"]["persona"]
        assert persona["bot_name"] == "麦麦"
        assert changed["agents.toml"] == ["agents.bot_name -> agents.streamer.persona.bot_name", "meta.version"]

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

    def test_hooks_outside_interval_skipped(self):
        """target <= old 或 > baseline 的钩子不执行"""
        raw = {"agents.toml": {"meta": {"version": "2.0.31"}, "agents": {"bot_name": "麦麦"}}}
        upgrade.register_file_hook("agents.toml", "sample", "2.0.31", sample_hook_v2_0_31)
        try:
            changed = advance_file_versions(raw)
        finally:
            upgrade._FILE_HOOKS.pop("agents.toml", None)

        assert changed == {}
        # 版本已在基线：旧键不被搬动（区间外不执行）
        assert "bot_name" in raw["agents.toml"]["agents"]

    def test_missing_version_raises(self):
        """存在文件缺 [meta].version → 硬错（ConfigValidationError）"""
        raw = {"agents.toml": {"agents": {}}}
        with pytest.raises(ConfigValidationError, match="version"):
            advance_file_versions(raw)

    def test_cross_file_hook_bumps_both_versions(self):
        """跨文件钩子：双写 + 双版本同升"""
        raw = {
            "agents.toml": {"meta": {"version": "2.0.30"}, "agents": {"sample_migrated": True}},
            "tools.toml": {"meta": {"version": "2.0.31"}, "tools": {}},
        }
        upgrade.register_cross_file_hook(
            "agents.toml", "tools.toml", "mover", "2.0.31", sample_cross_hook
        )
        try:
            changed = advance_file_versions(raw)
        finally:
            upgrade._FILE_HOOKS.pop("agents.toml", None)

        # 宿主：标记搬走 + 版本推进
        assert "sample_migrated" not in raw["agents.toml"]["agents"]
        assert raw["agents.toml"]["meta"]["version"] == CONFIG_BASELINE_VERSION
        # 目标：收到数据 + 版本同升（其原值已在基线，也被同升保持）
        assert raw["tools.toml"]["tools"]["sample_received"] is True
        assert raw["tools.toml"]["meta"]["version"] == CONFIG_BASELINE_VERSION
        assert "tools.toml" in changed


# ---------------------------------------------------------------------------
# 加载管线闭环（真实文件）
# ---------------------------------------------------------------------------


class TestVersionPipeline:
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
        """旧版本 + 已注册钩子 → load 推进并写回基线（闭环）"""
        generate_default_configs(tmp_path)
        self._write_version(tmp_path, "agents.toml", "2.0.30")

        # 旧版布局：[agents] 段顶层带 bot_name（新版归属 streamer.persona）
        agents_path = tmp_path / "agents.toml"
        content = agents_path.read_text(encoding="utf-8-sig")
        content = content.replace("[agents]\n", '[agents]\nbot_name = "麦麦"\n', 1)
        agents_path.write_text(content, encoding="utf-8-sig")

        upgrade.register_file_hook("agents.toml", "sample", "2.0.31", sample_hook_v2_0_31)
        try:
            config, _report = load_config_dir(tmp_path)
        finally:
            upgrade._FILE_HOOKS.pop("agents.toml", None)

        assert get_config_version(tmp_path, "agents.toml") == CONFIG_BASELINE_VERSION
        assert config["agents"]["agents"]["streamer"]["persona"]["bot_name"] == "麦麦"
        # 二次 load：版本已在基线，无进一步写盘
        _config2, report2 = load_config_dir(tmp_path)
        assert not report2.has_drift

    def test_independent_advance(self, tmp_path: Path):
        """改单文件版本不带动他文件（独立递增）"""
        generate_default_configs(tmp_path)
        self._write_version(tmp_path, "tools.toml", "2.0.30")

        load_config_dir(tmp_path)

        assert get_config_version(tmp_path, "tools.toml") == CONFIG_BASELINE_VERSION
        # 其他文件版本保持基线不动（未被降级或重写推进）
        assert get_config_version(tmp_path, "agents.toml") == CONFIG_BASELINE_VERSION
        assert get_config_version(tmp_path, "infra.toml") == CONFIG_BASELINE_VERSION

    def test_missing_version_hard_fail(self, tmp_path: Path):
        """删 [meta].version → load 硬错"""
        generate_default_configs(tmp_path)
        path = tmp_path / "tools.toml"
        content = path.read_text(encoding="utf-8-sig")
        content = content.replace('version = "2.0.31"', "", 1)
        path.write_text(content, encoding="utf-8-sig")

        with pytest.raises(ConfigValidationError, match="version"):
            load_config_dir(tmp_path)
