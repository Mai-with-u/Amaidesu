"""旧配置文件迁移

将根目录的旧 config.toml 迁移到 config/ 目录下的 5 个拆分文件。
自动备份旧文件到 config/old/，丢弃已确认的死配置。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import tomlkit

from src.modules.logging import get_logger

logger = get_logger("ConfigMigration")

# 确认的死配置（代码中已无引用）
_DEAD_SECTIONS = {"dg_lab", "spark_rtasr", "http_server"}

# section 前缀 → 目标文件 映射
_SECTION_MAP: dict[str, str] = {
    "meta": "core.toml",
    "type": "core.toml",
    "general": "core.toml",
    "persona": "core.toml",
    "maicore": "core.toml",
    "context": "core.toml",
    "dashboard": "core.toml",
    "event_bus": "core.toml",
    "logging": "core.toml",
    "mcp": "core.toml",
    "pipelines": "core.toml",
    "llm": "model.toml",
    "llm_fast": "model.toml",
    "vlm": "model.toml",
    "llm_local": "model.toml",
    "collectors": "input.toml",
    "deciders": "decision.toml",
    "handlers": "output.toml",
}

# 旧路径前缀 → 新 section 名
_LEGACY_PREFIX_MAP: dict[str, str] = {
    "providers.input": "collectors",
    "providers.output": "handlers",
    "providers.decision": "deciders",
}


@dataclass
class MigrationReport:
    """迁移结果报告"""

    migrated_sections: list[str] = field(default_factory=list)
    dropped_sections: list[str] = field(default_factory=list)
    backup_path: Path | None = None
    errors: list[str] = field(default_factory=list)


def _resolve_target_file(section_name: str) -> str | None:
    """根据 section 名确定目标文件

    处理嵌套 section（如 collectors.stt）和旧路径（如 providers.input.stt）。
    """
    # 死配置直接丢弃
    if section_name in _DEAD_SECTIONS:
        return None

    # 精确匹配
    if section_name in _SECTION_MAP:
        return _SECTION_MAP[section_name]

    # 前缀匹配（如 "pipelines.rate_limit" → core.toml）
    top_key = section_name.split(".")[0]
    if top_key in _SECTION_MAP:
        return _SECTION_MAP[top_key]

    # 旧路径匹配（providers.input.xxx → input.toml）
    for old_prefix, new_name in _LEGACY_PREFIX_MAP.items():
        if section_name.startswith(old_prefix):
            return _SECTION_MAP.get(new_name)

    return None


def _transform_section_name(section_name: str) -> str:
    """转换旧路径 section 名到新格式

    例: "providers.input.stt" → "collectors.stt"
    """
    for old_prefix, new_name in _LEGACY_PREFIX_MAP.items():
        if section_name.startswith(old_prefix + "."):
            suffix = section_name[len(old_prefix) + 1 :]
            return f"{new_name}.{suffix}"
        if section_name == old_prefix:
            return new_name
    return section_name


def migrate_old_config(
    old_config_path: Path,
    new_config_dir: Path,
) -> MigrationReport:
    """将旧 config.toml 迁移到新的多文件结构

    Args:
        old_config_path: 旧 config.toml 路径（通常是项目根目录）
        new_config_dir: config/ 目录路径

    Returns:
        MigrationReport: 迁移结果报告
    """
    report = MigrationReport()

    if not old_config_path.exists():
        report.errors.append(f"旧配置文件不存在: {old_config_path}")
        return report

    # 读取旧配置（保留注释用 tomlkit，但迁移时只需要数据）
    with open(old_config_path, "r", encoding="utf-8") as f:
        doc = tomlkit.load(f)

    # 按目标文件分组
    file_groups: dict[str, tomlkit.TOMLDocument] = {}

    for key in list(doc.keys()):
        # 遗留 [providers.*] 格式：递归处理子表，分发到对应文件
        if key == "providers":
            providers_table = doc[key]
            if hasattr(providers_table, "keys"):
                for sub_key in list(providers_table.keys()):
                    legacy_prefix = f"providers.{sub_key}"
                    new_name = _LEGACY_PREFIX_MAP.get(legacy_prefix)
                    if new_name:
                        target = _SECTION_MAP.get(new_name)
                        if target:
                            if target not in file_groups:
                                file_groups[target] = tomlkit.document()
                            file_groups[target][new_name] = providers_table[sub_key]
                            report.migrated_sections.append(f"[{legacy_prefix}] → {target}")
                            logger.info(f"迁移遗留配置: [{legacy_prefix}] → {target}")
            continue

        target_file = _resolve_target_file(key)

        if target_file is None:
            top_key = key.split(".")[0]
            if top_key in _DEAD_SECTIONS:
                report.dropped_sections.append(key)
                logger.info(f"丢弃死配置: [{key}]")
            else:
                report.dropped_sections.append(key)
                logger.warning(f"未知配置段，丢弃: [{key}]")
            continue

        if target_file not in file_groups:
            file_groups[target_file] = tomlkit.document()

        new_name = _transform_section_name(key)
        file_groups[target_file][new_name] = doc[key]
        report.migrated_sections.append(f"[{key}] → {target_file}")

    # 备份旧文件
    new_config_dir.mkdir(parents=True, exist_ok=True)
    old_dir = new_config_dir / "old"
    old_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"config_{timestamp}.toml"
    report.backup_path = old_dir / backup_name
    shutil.copy2(old_config_path, report.backup_path)
    logger.info(f"旧配置已备份到: {report.backup_path}")

    # 写入 5 个新文件
    expected_files = ["core.toml", "model.toml", "input.toml", "decision.toml", "output.toml"]
    for fname in expected_files:
        file_path = new_config_dir / fname
        if fname in file_groups:
            content = tomlkit.dumps(file_groups[fname])
            file_path.write_text(content, encoding="utf-8")
            logger.info(f"已写入 {fname}")
        else:
            logger.debug(f"{fname}: 无对应配置段，跳过（首次运行时会自动生成）")

    logger.info(
        f"迁移完成: {len(report.migrated_sections)} 个 section 已迁移, {len(report.dropped_sections)} 个 section 已丢弃"
    )

    return report
