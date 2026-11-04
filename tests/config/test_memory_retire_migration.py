"""旧记忆配置退役迁移测试（agents.toml v2.0.51 / infra.toml v2.0.45）

覆盖：
- agents.toml 的 ``[agents.streamer.memory]`` 整段删除（旧的三份记忆已停写停注入）；
- infra.toml 的 ``[maibot_sync]`` 段 ``share_notes`` 与 ``send_recap`` 键删除（共享长期笔记同步与回顾推送停用）；
- 两者都写回磁盘、版本推进到各自链上界，二次加载零写回（幂等）；
- 每个文件的钩子版本步严格递增且链尾高于生成基线（新生成的文件不会"天然高于"全部钩子）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit

from src.modules.config.file_meta import CONFIG_BASELINE_VERSION
from src.modules.config.multi_file_loader import get_config_version, load_config_dir
from src.modules.config.upgrade import _FILE_HOOKS, _version_tuple
from tests.config.helpers import read_doc, rewind_and_append, tail_version

# v2.0.50 时代的 [agents.streamer.memory] 段（v2.0.51 钩子整段删除）
_MEMORY_SECTION = (
    "\n[agents.streamer.memory]\n"
    "recap_enabled = true\n"
    "recap_max_length = 500\n"
    "recap_inject_count = 1\n"
    "roster_enabled = true\n"
    "roster_min_interactions = 3\n"
    "roster_batch_max = 5\n"
    "profile_max_length = 400\n"
    "notebook_max_chars = 2000\n"
    "long_memory_enabled = true\n"
    "long_memory_max_chars = 4000\n"
    "long_memory_stale_sessions = 6\n"
)


def _seed_streamer_memory(config_dir: Path) -> None:
    """把旧的 memory 段加回 agents.toml，并把版本真正拨到该钩子的前一版。

    ``rewind_and_append`` 按 ``[meta].version`` 的正则改写版本串（写死旧版本号在
    基线变动后会失配），随后把旧段追加成文件末尾的新表。种子写完当场校验版本与段
    都落地，避免"没拨回去"的静默空测。
    """
    path = config_dir / "agents.toml"
    assert "[agents.streamer.memory]" not in path.read_text(encoding="utf-8-sig")
    rewind_and_append(config_dir, "agents.toml", "2.0.50", _MEMORY_SECTION)

    seeded = read_doc(config_dir, "agents.toml")
    assert seeded["meta"]["version"] == "2.0.50"
    assert seeded["agents"]["streamer"]["memory"]["recap_enabled"] is True


def _seed_share_notes(config_dir: Path) -> None:
    """把两个旧键加回 infra.toml 的 ``[maibot_sync]`` 段，并把版本拨到该钩子的前一版。

    ``[maibot_sync]`` 是 infra.toml 的最后一个段，追加在文件尾的键就落在该段内；
    追加后读回确认键确实在 ``[maibot_sync]`` 下，段顺序变化时这里先红而不是空测。
    """
    rewind_and_append(config_dir, "infra.toml", "2.0.44", "\nshare_notes = true\nsend_recap = true\n")

    seeded = read_doc(config_dir, "infra.toml")
    assert seeded["meta"]["version"] == "2.0.44"
    assert seeded["maibot_sync"]["share_notes"] is True
    assert seeded["maibot_sync"]["send_recap"] is True


def test_streamer_memory_section_removed_and_written_back(tmp_path: Path):
    """旧配置构造 → 加载 → 磁盘上该段被删、版本推进到链上界。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_streamer_memory(tmp_path)

    load_config_dir(tmp_path)

    doc = tomlkit.parse((tmp_path / "agents.toml").read_text(encoding="utf-8-sig"))
    assert "memory" not in doc["agents"]["streamer"]
    assert get_config_version(tmp_path, "agents.toml") == tail_version("agents.toml")


def test_maibot_sync_share_notes_removed_and_written_back(tmp_path: Path):
    """旧配置构造 → 加载 → 磁盘上两个旧键都被删、版本推进到链上界。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_share_notes(tmp_path)

    load_config_dir(tmp_path)

    doc = tomlkit.parse((tmp_path / "infra.toml").read_text(encoding="utf-8-sig"))
    assert "share_notes" not in doc["maibot_sync"]
    assert "send_recap" not in doc["maibot_sync"]
    assert get_config_version(tmp_path, "infra.toml") == tail_version("infra.toml")


def test_memory_retire_migrations_are_idempotent(tmp_path: Path):
    """再次加载零写回：两个钩子对已迁移数据都不产生变更。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    generate_default_configs(tmp_path)
    _seed_streamer_memory(tmp_path)
    _seed_share_notes(tmp_path)
    load_config_dir(tmp_path)
    agents_mtime = (tmp_path / "agents.toml").stat().st_mtime_ns
    infra_mtime = (tmp_path / "infra.toml").stat().st_mtime_ns

    load_config_dir(tmp_path)

    assert (tmp_path / "agents.toml").stat().st_mtime_ns == agents_mtime
    assert (tmp_path / "infra.toml").stat().st_mtime_ns == infra_mtime


def test_file_hook_chain_tail_is_above_baseline() -> None:
    """每个文件的钩子链尾（最大 target）高于生成基线。

    链尾若不高过 ``CONFIG_BASELINE_VERSION``，新生成的配置文件版本天然已"越过"全部
    钩子，升级钩子再也不会执行，旧布局会被当成未知段硬错挡住启动。旧钩子的 target
    允许低于基线——它们承担基线之前老配置的升级（如 agents.toml 的 v2.0.32）。
    """
    baseline = _version_tuple(CONFIG_BASELINE_VERSION)
    assert _FILE_HOOKS, "升级钩子注册表为空（upgrade 模块的登记丢了）"
    for file_name in _FILE_HOOKS:
        tail = _version_tuple(tail_version(file_name))
        assert tail > baseline, f"{file_name} 的钩子链尾 {tail} 不高于基线 {CONFIG_BASELINE_VERSION}"


@pytest.mark.parametrize(
    ("file_name", "hook_name"),
    [
        ("agents.toml", "drop_streamer_memory_section"),
        ("infra.toml", "drop_maibot_sync_share_notes"),
    ],
)
def test_memory_retire_hook_target_is_next_step_after_previous_chain(file_name: str, hook_name: str) -> None:
    """本次登记的钩子 target 严格递增：恰为此前链上界的下一位，且高于生成基线。"""
    hooks = _FILE_HOOKS[file_name]
    target = next(_version_tuple(hook.target_version) for hook in hooks if hook.name == hook_name)
    previous = max(
        _version_tuple(hook.target_version) for hook in hooks if _version_tuple(hook.target_version) < target
    )
    baseline = _version_tuple(CONFIG_BASELINE_VERSION)

    assert target > baseline, f"{file_name} 的 {hook_name} target {target} 不高于基线 {CONFIG_BASELINE_VERSION}"
    assert target == (previous[0], previous[1], previous[2] + 1), (
        f"{file_name} 的 {hook_name} target {target} 不是此前链上界 {previous} 的下一位"
    )
