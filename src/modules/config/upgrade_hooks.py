"""配置升级钩子系统（v2.0.0）

提供版本驱动的配置迁移机制。
当配置版本跨越特定版本号时，自动执行对应的迁移函数。

使用方式：
    1. 定义迁移函数：def _my_migrate(data: dict) -> list[str]
    2. 注册钩子：在 CONFIG_UPGRADE_HOOKS 中添加 ConfigUpgradeHook
    3. 启动时自动触发：apply_upgrade_hooks(data, file, old_ver, new_ver)

AGENTS.md 钩子契约：
- **原地修改**：hook 接收 dict 并直接修改，不返回新对象
- **幂等**：重复执行结果一致（首次执行后旧值已不存在，再次执行无事发生）
- **返回变更路径列表**：hook 返回本次修改的字段路径列表
- **每条 hook 配单元测试**：旧结构输入 → → 断言新结构输出
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

ConfigMigrateCallable = Callable[[dict[str, Any]], list[str]]


@dataclass(frozen=True)
class ConfigUpgradeHook:
    """配置升级钩子，在跨过指定版本时执行一次。

    Attributes:
        target_version: 触发此钩子的目标版本号（如 "2.0.0"）
        config_file: 作用的配置文件名（如 "core.toml"）
        migrate: 迁移函数，接收配置字典，原地修改，返回变更的字段路径列表
    """

    target_version: str
    config_file: str
    migrate: ConfigMigrateCallable


@dataclass
class UpgradeResult:
    """升级结果

    Attributes:
        data: 迁移后的配置数据
        migrated: 是否发生了迁移
        reasons: 迁移原因列表（格式："{version}:{field_path}"）
    """

    data: dict[str, Any]
    migrated: bool
    reasons: list[str]


# ---------------------------------------------------------------------------
# 0.5.4 → 1.0.0 阶段迁移 hooks（沿用历史 mainosaba 改名）
# ---------------------------------------------------------------------------


def _migrate_mainosaba_to_text_adv_game(data: dict[str, Any]) -> list[str]:
    """兼容期 hook（旧 input.toml 已废弃；v2.0.0 此函数不再被新文件触发，保留供回滚）。

    - ``[collectors]`` 段键 ``mainosaba`` → ``text_adv_game``（子配置整体保留）
    - ``[collectors].enabled`` 列表内 ``mainosaba`` → ``text_adv_game``

    原地修改、幂等，返回变更路径列表。
    """
    changed: list[str] = []
    collectors = data.get("collectors")
    if isinstance(collectors, dict):
        if "mainosaba" in collectors:
            collectors["text_adv_game"] = collectors.pop("mainosaba")
            changed.append("collectors.text_adv_game")
        enabled = collectors.get("enabled")
        if isinstance(enabled, list) and "mainosaba" in enabled:
            collectors["enabled"] = ["text_adv_game" if v == "mainosaba" else v for v in enabled]
            changed.append("collectors.enabled")
    return changed


# ---------------------------------------------------------------------------
# 2.0.0 跨域迁移 hooks（核心改造）
# ---------------------------------------------------------------------------


def _migrate_core_2_0_0(data: dict[str, Any]) -> list[str]:
    """core.toml 2.0.0：删 ``[maicore]`` + 改造 ``[context]`` 为 ContextAssembler 配置。

    旧 [maicore] 段（host/port/token）→ 2.0.0 单进程不需要 MaiCore 连接，
    直接删除（字段已无人引用，旧客户端连接走 [mcp] 段）。

    旧 [context] 段（storage_type/max_messages_per_session/...）→ 新
    [context] 段（enabled/memory_recall_viewers/memory_recall_long_term/
    cache_ttl_ms），旧字段全部丢弃（语义已变：会话存储职责下放给 memory 后端）。

    原地修改、幂等，返回变更路径列表。
    """
    changed: list[str] = []
    if "maicore" in data:
        data.pop("maicore")
        changed.append("maicore")

    context = data.get("context")
    if context is None:
        data["context"] = {
            "enabled": True,
            "memory_recall_viewers": 3,
            "memory_recall_long_term": 3,
            "cache_ttl_ms": 1000,
        }
        changed.append("context")
        return changed

    if isinstance(context, dict):
        old_keys = set(context.keys())
        new_context = {
            "enabled": bool(context.get("enabled", True)),
            "memory_recall_viewers": 3,
            "memory_recall_long_term": 3,
            "cache_ttl_ms": 1000,
        }
        new_keys = set(new_context.keys())
        data["context"] = new_context
        if old_keys != new_keys:
            changed.append("context")
    return changed


def _migrate_model_2_0_0(data: dict[str, Any]) -> list[str]:
    """model.toml 2.0.0：``llm_outline`` → ``llm_agenda``（Outline→Agenda 命名统一）。

    原地修改、幂等；重复执行时 ``llm_outline`` 已不存在，无事发生。

    同时（可选）补齐缺失的 ``llm_agenda`` 默认占位，避免其他依赖该 profile 的
    模块读取时报 KeyError。
    """
    changed: list[str] = []
    if "llm_outline" in data:
        outline_cfg = data.pop("llm_outline")
        # 仅在用户没显式配置 llm_agenda 时迁移
        if "llm_agenda" not in data:
            data["llm_agenda"] = outline_cfg
            changed.append("llm_agenda")
            changed.append("llm_outline")
    return changed


def _migrate_tools_2_0_0(data: dict[str, Any]) -> list[str]:
    """tools.toml 2.0.0：补齐缺失的默认结构。

    旧 [collectors]/[handlers] 段通过 CrossFileMigration 合并到
    ``[tools.perception.config]`` / ``[tools.output.config]``，本 hook 仅负责
    兜底：用户全新升级时若 tools.toml 已生成但无任何工具包，补充 enabled 列表
    和默认结构。

    原地修改、幂等。
    """
    changed: list[str] = []
    tools = data.get("tools")
    if not isinstance(tools, dict):
        # tools 段不存在或类型错误 → 重建
        data["tools"] = {
            "enabled": ["perception", "output"],
            "perception": None,
            "output": None,
        }
        changed.append("tools")
        return changed

    # enabled 列表兜底
    if "enabled" not in tools or not isinstance(tools.get("enabled"), list):
        tools["enabled"] = ["perception", "output"]
        changed.append("tools.enabled")
    return changed


def _migrate_agents_2_0_0(data: dict[str, Any]) -> list[str]:
    """agents.toml 2.0.0：补齐缺失的默认结构。

    旧 [deciders] 段通过 CrossFileMigration 合并到 ``[agents]`` 段，本 hook 负责：
    1. ``enabled`` 列表兜底（含旧 enabled 列表的迁移项）
    2. 默认 streamer 子配置

    原地修改、幂等。
    """
    changed: list[str] = []
    agents = data.get("agents")
    if not isinstance(agents, dict):
        data["agents"] = {
            "enabled": ["streamer"],
            "streamer": {
                "planner_llm": "llm_fast",
                "replyer_llm": "llm",
                "reply_probability": 0.7,
                "budget": {
                    "max_rounds": 3,
                    "timeout_ms": 5000,
                    "finish_action": "say",
                },
                "room_state_enabled": True,
                "room_state_cold_timeout_ms": 60000,
                "room_state_llm_summary_interval_ms": 60000,
            },
            "game": None,
        }
        changed.append("agents")
        return changed

    if "enabled" not in agents or not isinstance(agents.get("enabled"), list):
        agents["enabled"] = ["streamer"]
        changed.append("agents.enabled")
    return changed


def _migrate_memory_2_0_0(data: dict[str, Any]) -> list[str]:
    """memory.toml 2.0.0：兜底 backend 默认值。

    新文件首次生成时 backend="simple" 已写入，但用户从旧 config.toml 迁移时
    可能没有 [memory] 段。这里补默认。

    原地修改、幂等。
    """
    changed: list[str] = []
    memory = data.get("memory")
    if not isinstance(memory, dict):
        data["memory"] = {"backend": "simple", "simple": None, "amemorix": None}
        changed.append("memory")
        return changed
    if "backend" not in memory:
        memory["backend"] = "simple"
        changed.append("memory.backend")
    return changed


def _migrate_storage_2_0_0(data: dict[str, Any]) -> list[str]:
    """storage.toml 2.0.0：兜底 [storage.sqlite] 默认值。

    原地修改、幂等。
    """
    changed: list[str] = []
    storage = data.get("storage")
    if not isinstance(storage, dict):
        data["storage"] = {
            "sqlite": {
                "db_path": "data/amaidesu.db",
                "wal": True,
                "busy_timeout_ms": 5000,
                "foreign_keys": True,
            }
        }
        changed.append("storage")
        return changed
    sqlite = storage.get("sqlite")
    if not isinstance(sqlite, dict):
        storage["sqlite"] = {
            "db_path": "data/amaidesu.db",
            "wal": True,
            "busy_timeout_ms": 5000,
            "foreign_keys": True,
        }
        changed.append("storage.sqlite")
    return changed


def _migrate_background_2_0_0(data: dict[str, Any]) -> list[str]:
    """background.toml 2.0.0：兜底默认配置。

    原地修改、幂等。
    """
    changed: list[str] = []
    bg = data.get("background")
    if not isinstance(bg, dict):
        data["background"] = {
            "light_tick_ms": 5000,
            "compressor": {"concurrency": 1, "queue_max": 100},
        }
        changed.append("background")
        return changed
    if "light_tick_ms" not in bg:
        bg["light_tick_ms"] = 5000
        changed.append("background.light_tick_ms")
    compressor = bg.get("compressor")
    if not isinstance(compressor, dict):
        bg["compressor"] = {"concurrency": 1, "queue_max": 100}
        changed.append("background.compressor")
    return changed


# ---------------------------------------------------------------------------
# 升级钩子注册表
# ---------------------------------------------------------------------------


CONFIG_UPGRADE_HOOKS: tuple[ConfigUpgradeHook, ...] = (
    # 0.5.4 历史钩子（保留供回滚，新文件不再触发）
    ConfigUpgradeHook(
        target_version="0.5.4",
        config_file="input.toml",
        migrate=_migrate_mainosaba_to_text_adv_game,
    ),
    # 2.0.0 七文件全量改造
    ConfigUpgradeHook(
        target_version="2.0.0",
        config_file="core.toml",
        migrate=_migrate_core_2_0_0,
    ),
    ConfigUpgradeHook(
        target_version="2.0.0",
        config_file="model.toml",
        migrate=_migrate_model_2_0_0,
    ),
    ConfigUpgradeHook(
        target_version="2.0.0",
        config_file="tools.toml",
        migrate=_migrate_tools_2_0_0,
    ),
    ConfigUpgradeHook(
        target_version="2.0.0",
        config_file="agents.toml",
        migrate=_migrate_agents_2_0_0,
    ),
    ConfigUpgradeHook(
        target_version="2.0.0",
        config_file="memory.toml",
        migrate=_migrate_memory_2_0_0,
    ),
    ConfigUpgradeHook(
        target_version="2.0.0",
        config_file="storage.toml",
        migrate=_migrate_storage_2_0_0,
    ),
    ConfigUpgradeHook(
        target_version="2.0.0",
        config_file="background.toml",
        migrate=_migrate_background_2_0_0,
    ),
)


# ---------------------------------------------------------------------------
# 版本范围与 apply
# ---------------------------------------------------------------------------


def _parse_version(version: str) -> tuple[int, ...]:
    """解析版本字符串为可比较的元组。"""
    return tuple(int(part) for part in version.split("."))


def _version_in_range(old_ver: str, target_ver: str, new_ver: str) -> bool:
    """检查 target_ver 是否在 (old_ver, new_ver] 范围内。

    即：old < target <= new 时返回 True。
    确保每个钩子在版本跨越时只执行一次。
    """
    old_parts = _parse_version(old_ver)
    target_parts = _parse_version(target_ver)
    new_parts = _parse_version(new_ver)
    return old_parts < target_parts <= new_parts


def apply_upgrade_hooks(
    data: dict[str, Any],
    config_file: str,
    old_ver: str,
    new_ver: str,
) -> UpgradeResult:
    """应用版本范围内的升级钩子。

    遍历所有注册的钩子，对匹配 config_file 且版本在 (old_ver, new_ver] 范围内的钩子执行迁移。
    """
    migrated_reasons: list[str] = []

    for hook in CONFIG_UPGRADE_HOOKS:
        if hook.config_file != config_file:
            continue
        if not _version_in_range(old_ver, hook.target_version, new_ver):
            continue

        hook_reasons = hook.migrate(data)
        for reason in hook_reasons:
            migrated_reasons.append(f"{hook.target_version}:{reason}")

    return UpgradeResult(
        data=data,
        migrated=bool(migrated_reasons),
        reasons=migrated_reasons,
    )
