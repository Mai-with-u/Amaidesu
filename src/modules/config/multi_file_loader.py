"""多文件配置加载器

从 config/ 目录加载 5 个 TOML 配置文件，使用 Pydantic Schema 验证并检测漂移。
首次运行时从 Schema 默认值生成带注释的配置文件。

配置文件结构:
    config/core.toml     - 核心系统配置（general, persona, maicore, context...）
    config/model.toml    - LLM/VLM 模型配置
    config/input.toml    - Input 阶段（collectors 启用列表 + 各 collector 配置）
    config/decision.toml - Decision 阶段（active decider + 各 decider 配置）
    config/output.toml   - Output 阶段（handlers 启用列表 + 各 handler 配置）
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import tomlkit

from src.modules.config.schemas.base import BaseConfig, DriftReport, _set_toml_value
from src.modules.config.core_schemas import CoreConfig
from src.modules.config.model_schemas import ModelConfig
from src.modules.logging import get_logger
from pydantic import BaseModel

logger = get_logger("MultiFileLoader")

CONFIG_VERSION = "0.4.0"

_CONFIG_FILES = ["core.toml", "model.toml", "input.toml", "decision.toml", "output.toml"]


def _backup_file(file_path: Path, config_dir: Path) -> Path | None:
    """备份配置文件到 config/old/ 目录"""
    if not file_path.exists():
        return None
    old_dir = config_dir / "old"
    old_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{file_path.stem}_{timestamp}{file_path.suffix}"
    backup_path = old_dir / backup_name
    shutil.copy2(file_path, backup_path)
    logger.info(f"已备份 {file_path.name} 到 {backup_path}")
    return backup_path


def _generate_core_toml() -> str:
    """生成 core.toml 内容（每个子配置为独立顶层 section）"""
    doc = tomlkit.document()
    doc.add(tomlkit.comment("核心系统配置 - Amaidesu"))
    doc.add(tomlkit.nl())

    core = CoreConfig()
    doc.add(tomlkit.comment(f'type = "{core.type}"'))
    doc["type"] = core.type
    doc.add(tomlkit.nl())

    for field_name, _field_info in CoreConfig.model_fields.items():
        if field_name == "type":
            continue

        field_value = getattr(core, field_name)

        if isinstance(field_value, BaseModel):
            sub_config = field_value.model_dump()
            sub_cls = type(field_value)
            table = tomlkit.table()

            for sub_name, sub_info in sub_cls.model_fields.items():
                if sub_info.description:
                    table.add(tomlkit.comment(sub_info.description))
                _set_toml_value(table, sub_name, sub_config.get(sub_name))

            doc[field_name] = table
            doc.add(tomlkit.nl())
        elif field_name == "pipelines" and field_value:
            pipelines_table = tomlkit.table()
            for pipeline_name, pipeline_config in field_value.items():
                _set_toml_value(pipelines_table, pipeline_name, pipeline_config)
            doc[field_name] = pipelines_table
            doc.add(tomlkit.nl())
        else:
            if _field_info.description:
                doc.add(tomlkit.comment(_field_info.description))
            doc[field_name] = field_value
            doc.add(tomlkit.nl())

    return tomlkit.dumps(doc)


def _generate_model_toml() -> str:
    """生成 model.toml 内容"""
    doc = tomlkit.document()
    doc.add(tomlkit.comment("模型配置 - LLM/VLM 参数"))
    doc.add(tomlkit.nl())

    model = ModelConfig()
    doc["type"] = model.type
    doc.add(tomlkit.nl())

    for field_name, field_info in ModelConfig.model_fields.items():
        if field_name == "type":
            continue
        field_value = getattr(model, field_name)

        if isinstance(field_value, BaseModel):
            sub_config = field_value.model_dump()
            sub_table = tomlkit.table()

            nested_cls = type(field_value)
            for sub_name, sub_info in nested_cls.model_fields.items():
                if sub_info.description:
                    sub_table.add(tomlkit.comment(sub_info.description))
                sub_table[sub_name] = sub_config[sub_name]

            if field_info.description:
                doc.add(tomlkit.comment(field_info.description))
            doc[field_name] = sub_table
            doc.add(tomlkit.nl())
        else:
            if field_info.description:
                doc.add(tomlkit.comment(field_info.description))
            doc[field_name] = field_value
            doc.add(tomlkit.nl())

    return tomlkit.dumps(doc)


def _generate_phase_toml(phase: str) -> str:
    """生成阶段配置文件（input.toml / decision.toml / output.toml）"""
    doc = tomlkit.document()

    if phase == "input":
        doc.add(tomlkit.comment("Input 阶段配置 - Collectors"))
        doc.add(tomlkit.nl())
        table = tomlkit.table()
        table.add(tomlkit.comment("启用的 Collector 列表"))
        table["enabled"] = []
        doc["collectors"] = table

    elif phase == "decision":
        doc.add(tomlkit.comment("Decision 阶段配置 - Deciders"))
        doc.add(tomlkit.nl())
        table = tomlkit.table()
        table.add(tomlkit.comment("启用的 Decider 列表（可多选，所有启用的 Decider 将并行处理消息）"))
        table["enabled"] = ["maibot"]
        table.add(tomlkit.comment("可用 Decider（取消注释并添加到上方 enabled 列表即可启用）"))
        table.add(tomlkit.comment('  "llm",      # LLM 直接决策'))
        table.add(tomlkit.comment('  "maicraft", # MaiCraft 弹幕游戏'))
        table.add(tomlkit.comment('  "replay",   # 输入重放（调试用）'))
        doc["deciders"] = table

    elif phase == "output":
        doc.add(tomlkit.comment("Output 阶段配置 - Handlers"))
        doc.add(tomlkit.nl())
        table = tomlkit.table()
        table.add(tomlkit.comment("启用的 Handler 列表"))
        table["enabled"] = ["subtitle", "vts"]
        table.add(tomlkit.comment("是否并发渲染"))
        table["concurrent_rendering"] = True
        table.add(tomlkit.comment("错误处理策略: continue | stop"))
        table["error_handling"] = "continue"
        table.add(tomlkit.comment("单个 Handler 渲染超时（毫秒），0 表示不限制"))
        table["render_timeout_ms"] = 10000
        doc["handlers"] = table

    return tomlkit.dumps(doc)


def generate_default_configs(config_dir: Path) -> None:
    """首次运行：从 Schema 生成 5 个默认配置文件

    Args:
        config_dir: config/ 目录路径
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"生成默认配置到 {config_dir}")

    # core.toml
    core_path = config_dir / "core.toml"
    core_path.write_text(_generate_core_toml(), encoding="utf-8")
    logger.info("已生成 core.toml")

    # model.toml
    model_path = config_dir / "model.toml"
    model_path.write_text(_generate_model_toml(), encoding="utf-8")
    logger.info("已生成 model.toml")

    # input.toml
    input_path = config_dir / "input.toml"
    input_path.write_text(_generate_phase_toml("input"), encoding="utf-8")
    logger.info("已生成 input.toml")

    # decision.toml
    decision_path = config_dir / "decision.toml"
    decision_path.write_text(_generate_phase_toml("decision"), encoding="utf-8")
    logger.info("已生成 decision.toml")

    # output.toml
    output_path = config_dir / "output.toml"
    output_path.write_text(_generate_phase_toml("output"), encoding="utf-8")
    logger.info("已生成 output.toml")


def _load_and_validate_schema(
    file_path: Path,
    schema_cls: type[BaseConfig],
) -> tuple[dict[str, Any], DriftReport]:
    """加载单个 Schema 配置文件并验证

    Args:
        file_path: 配置文件路径
        schema_cls: Pydantic Schema 类

    Returns:
        (model_dump 字典, 漂移报告)
    """
    with open(file_path, "r", encoding="utf-8") as f:
        doc = tomlkit.load(f)

    raw_data = doc.unwrap()
    instance, report = schema_cls.from_dict_with_drift_check(raw_data)

    if report.has_drift:
        for key in report.redundant:
            logger.warning(f"{file_path.name}: 检测到冗余配置项 '{key}'（代码中已不存在）")
        for key in report.missing:
            logger.info(f"{file_path.name}: 补充缺失配置项 '{key}'（使用默认值）")

    return instance.model_dump(), report


def load_config_dir(
    config_dir: Path,
) -> tuple[dict[str, Any], DriftReport]:
    """加载 config/ 目录下所有配置文件

    Args:
        config_dir: config/ 目录路径

    Returns:
        (合并后的配置字典, 综合漂移报告)

    The merged config dict has keys:
        "core", "model", "input", "decision", "output"
    """
    combined = DriftReport()
    result: dict[str, Any] = {}

    # core.toml → CoreConfig
    core_path = config_dir / "core.toml"
    if core_path.exists():
        core_data, core_report = _load_and_validate_schema(core_path, CoreConfig)
        result["core"] = core_data
        combined.redundant.extend(f"core.{r}" for r in core_report.redundant)
        combined.missing.extend(f"core.{m}" for m in core_report.missing)

    # model.toml → ModelConfig
    model_path = config_dir / "model.toml"
    if model_path.exists():
        model_data, model_report = _load_and_validate_schema(model_path, ModelConfig)
        result["model"] = model_data
        combined.redundant.extend(f"model.{r}" for r in model_report.redundant)
        combined.missing.extend(f"model.{m}" for m in model_report.missing)

    # input.toml → raw dict（组件配置在各 Manager 中单独验证）
    input_path = config_dir / "input.toml"
    if input_path.exists():
        with open(input_path, "r", encoding="utf-8") as f:
            result["input"] = tomlkit.load(f).unwrap()

    # decision.toml → raw dict
    decision_path = config_dir / "decision.toml"
    if decision_path.exists():
        with open(decision_path, "r", encoding="utf-8") as f:
            result["decision"] = tomlkit.load(f).unwrap()

    # output.toml → raw dict
    output_path = config_dir / "output.toml"
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            result["output"] = tomlkit.load(f).unwrap()

    return result, combined


def needs_generation(config_dir: Path) -> bool:
    """检查是否需要生成默认配置

    Returns:
        True 如果 config/ 目录不存在或没有任何 .toml 文件
    """
    if not config_dir.exists():
        return True
    toml_files = list(config_dir.glob("*.toml"))
    return len(toml_files) == 0


def get_config_version(config_dir: Path) -> str | None:
    """从 core.toml 读取配置版本号

    Args:
        config_dir: config/ 目录路径

    Returns:
        版本号字符串，如果不存在返回 None
    """
    core_path = config_dir / "core.toml"
    if not core_path.exists():
        return None
    with open(core_path, "r", encoding="utf-8") as f:
        doc = tomlkit.load(f)
    meta = doc.get("meta", {})
    version = meta.get("version")
    return str(version) if version else None
