"""配置升级钩子系统

提供版本驱动的配置迁移机制。
当配置版本跨越特定版本号时，自动执行对应的迁移函数。

使用方式：
    1. 定义迁移函数：def _my_migrate(data: dict) -> list[str]
    2. 注册钩子：在 CONFIG_UPGRADE_HOOKS 中添加 ConfigUpgradeHook
    3. 启动时自动触发：apply_upgrade_hooks(data, file, old_ver, new_ver)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

ConfigMigrateCallable = Callable[[dict[str, Any]], list[str]]


@dataclass(frozen=True)
class ConfigUpgradeHook:
    """配置升级钩子，在跨过指定版本时执行一次。

    Attributes:
        target_version: 触发此钩子的目标版本号（如 "0.4.0"）
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


# 升级钩子注册表
# 添加新钩子时，在元组中追加 ConfigUpgradeHook 即可
# 每个钩子在其 target_version 被跨越时执行一次
CONFIG_UPGRADE_HOOKS: tuple[ConfigUpgradeHook, ...] = ()


def _parse_version(version: str) -> tuple[int, ...]:
    """解析版本字符串为可比较的元组。

    Args:
        version: 版本字符串，如 "0.4.0"

    Returns:
        版本元组，如 (0, 4, 0)
    """
    return tuple(int(part) for part in version.split("."))


def _version_in_range(old_ver: str, target_ver: str, new_ver: str) -> bool:
    """检查 target_ver 是否在 (old_ver, new_ver] 范围内。

    即：old < target <= new 时返回 True。
    确保每个钩子在版本跨越时只执行一次。

    Args:
        old_ver: 当前配置的旧版本
        target_ver: 钩子的目标版本
        new_ver: 代码中的新版本

    Returns:
        是否在触发范围内
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

    Args:
        data: 配置数据字典（会被钩子原地修改）
        config_file: 配置文件名（如 "core.toml"）
        old_ver: 当前配置的版本号
        new_ver: 代码中的目标版本号

    Returns:
        UpgradeResult: 升级结果，包含迁移后的数据和变更原因

    Example:
        >>> result = apply_upgrade_hooks(data, "core.toml", "0.3.0", "0.4.0")
        >>> if result.migrated:
        ...     print(f"已迁移: {result.reasons}")
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
