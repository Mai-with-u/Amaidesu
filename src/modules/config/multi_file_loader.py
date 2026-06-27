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

import importlib
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Union, cast, get_args, get_origin

import tomlkit

from src.modules.config.schemas.base import BaseConfig, DriftReport, _set_toml_value
from src.modules.config.core_schemas import CoreConfig
from src.modules.config.model_schemas import ModelConfig
from src.modules.logging import get_logger
from pydantic import BaseModel

logger = get_logger("MultiFileLoader")

_PHASE_TO_SECTION: dict[str, str] = {
    "input": "collectors",
    "decision": "deciders",
    "output": "handlers",
}
_PHASE_TO_COMPONENTS_PKG: dict[str, str] = {
    "input": "src.stages.input.collectors",
    "decision": "src.stages.decision.deciders",
    "output": "src.stages.output.handlers",
}
_PHASE_TO_REGISTRY: dict[tuple[str, str], str] = {
    ("input", "_COLLECTORS"): "src.stages.input.registry",
    ("decision", "_DECIDERS"): "src.stages.decision.registry",
    ("output", "_HANDLERS"): "src.stages.output.registry",
}

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


def _unwrap_optional(annotation: Any) -> Any:
    """解包 Optional[X] / Union[X, None] → X；其他类型原样返回。

    仅当 Union 中只有一个非 None 参数时才解包，避免误判 Union[X, Y] 类联合类型。
    """
    origin = get_origin(annotation)
    if origin is Union:
        non_none_args = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none_args) == 1:
            return non_none_args[0]
    return annotation


def _placeholder_for_type(annotation: Any) -> Any:
    """根据字段注解生成占位符值。

    用于必填字段：避免直接调用 schema_cls()（会触发 ValidationError），
    而是根据类型返回中性的占位符，配合 `[必填]` 注释提示用户填写。
    """
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is list:
        return []
    if origin is dict:
        return {}
    if origin is tuple:
        return ()
    if origin is set:
        return []
    if origin is frozenset:
        return []
    if origin is Literal:
        return str(args[0]) if args else ""
    if annotation is str:
        return "请填写"
    if annotation is int:
        return 0
    if annotation is float:
        return 0.0
    if annotation is bool:
        return False
    if annotation is bytes:
        return b""
    return ""


def _extract_constraint_hints(field_info: Any) -> str:
    """从 Pydantic v2 Field 的 metadata 中提取 gt/ge/lt/le/length 等约束提示。

    用于在 `[必填]` 注释中追加约束条件，避免用户填入非法值。
    """
    hints: list[str] = []
    for meta in field_info.metadata:
        cls_name = type(meta).__name__
        if cls_name == "Gt":
            hints.append(f"需大于 {meta.gt}")
        elif cls_name == "Ge":
            hints.append(f"需大于等于 {meta.ge}")
        elif cls_name == "Lt":
            hints.append(f"需小于 {meta.lt}")
        elif cls_name == "Le":
            hints.append(f"需小于等于 {meta.le}")
        elif cls_name == "MinLen":
            hints.append(f"最少 {meta.min_length} 个字符")
        elif cls_name == "MaxLen":
            hints.append(f"最多 {meta.max_length} 个字符")
    return "，".join(hints)


def _schema_to_toml_table(schema_cls: type[BaseModel]) -> Any:
    """从 Pydantic v2 Schema 类（不实例化）生成 tomlkit Table 模板。

    处理规则：
    - 简单字段（key-value）先输出，子表字段（嵌套 BaseModel / 非空 dict 默认值）后输出，
      确保 TOML 语法合法（父表键值对必须在子表之前），且字段注释紧邻对应字段不产生孤儿注释。
    - 必填字段（is_required() 为 True）→ 占位符值 + `# [必填]` 注释
    - 有默认值（非 None、非空容器）→ 使用默认值 + description 注释
    - 默认值为 None 或空 dict/空 list → 跳过

    字段处理顺序在各类内部遵循 model_fields 的声明顺序，确保输出稳定。
    """
    table = tomlkit.table()

    # 按字段类型分两类：简单字段（key-value）与 子表字段（sub-table）
    simple_fields: list[tuple[str, Any, Any]] = []
    subtable_fields: list[tuple[str, Any, Any]] = []

    for field_name, field_info in schema_cls.model_fields.items():
        unwrapped = _unwrap_optional(field_info.annotation)

        is_nested_model = isinstance(unwrapped, type) and issubclass(unwrapped, BaseModel)
        is_subtable = False

        if is_nested_model:
            is_subtable = True
        elif not field_info.is_required():
            try:
                default_value = field_info.get_default(call_default_factory=True)
            except Exception:
                default_value = None
            # 非空 dict 默认值会变成子表，归入子表类（避免触发 tomlkit 重排导致孤儿注释）
            if isinstance(default_value, dict) and default_value:
                is_subtable = True

        target = subtable_fields if is_subtable else simple_fields
        target.append((field_name, field_info, unwrapped))

    # 第一遍：输出所有简单字段（key-value），注释与值紧邻
    for field_name, field_info, unwrapped in simple_fields:
        if field_info.is_required():
            desc = field_info.description or ""
            constraint_hint = _extract_constraint_hints(field_info)
            parts = ["[必填]"]
            if desc:
                parts.append(desc)
            if constraint_hint:
                parts.append(f"({constraint_hint})")
            table.add(tomlkit.comment(" ".join(parts)))
            table[field_name] = _placeholder_for_type(unwrapped)
            continue

        try:
            default_value = field_info.get_default(call_default_factory=True)
        except Exception:
            continue

        if default_value is None:
            continue

        if isinstance(default_value, (dict, list)) and not default_value:
            continue

        if field_info.description:
            table.add(tomlkit.comment(field_info.description))

        table[field_name] = default_value

    # 第二遍：输出所有子表字段（嵌套 BaseModel / 非空 dict 默认值）
    for field_name, field_info, unwrapped in subtable_fields:
        is_nested_model = isinstance(unwrapped, type) and issubclass(unwrapped, BaseModel)

        if is_nested_model:
            sub_table = _schema_to_toml_table(unwrapped)
        else:
            try:
                default_value = field_info.get_default(call_default_factory=True)
            except Exception:
                continue
            if not isinstance(default_value, dict) or not default_value:
                continue
            # 手动构造子表（避免 _set_toml_value 在父表上转子表触发重排）
            sub_table = tomlkit.table()
            for sub_key, sub_value in default_value.items():
                if isinstance(sub_value, dict):
                    inner_table = tomlkit.table()
                    for inner_key, inner_value in sub_value.items():
                        inner_table[inner_key] = inner_value
                    sub_table[sub_key] = inner_table
                else:
                    sub_table[sub_key] = sub_value

        # 字段注释：必填加 [必填] 标记，否则用 description
        if field_info.is_required():
            desc = field_info.description or ""
            if desc:
                table.add(tomlkit.comment(f"[必填] {desc}"))
            else:
                table.add(tomlkit.comment("[必填]"))
        elif field_info.description:
            table.add(tomlkit.comment(field_info.description))

        table[field_name] = sub_table

    return table


def _discover_components(phase: str) -> dict[str, type]:
    """发现某阶段所有已注册组件的 ConfigSchema 类。

    主动 import 阶段包，触发 @collector/@decider/@handler 装饰器注册，
    然后枚举对应 registry 中的组件，提取其 ConfigSchema 嵌套类。

    容错策略：组件包 import 失败（如 STT 缺少 torch 依赖）时，
    仅记录 warning 并返回空 dict，调用方降级到不追加组件模板的行为。
    """
    components_pkg = _PHASE_TO_COMPONENTS_PKG.get(phase)
    section_key = _PHASE_TO_SECTION.get(phase)
    registry_attr = {"input": "_COLLECTORS", "decision": "_DECIDERS", "output": "_HANDLERS"}.get(phase)
    if components_pkg is None or section_key is None or registry_attr is None:
        logger.warning(f"未知阶段: {phase}")
        return {}

    try:
        importlib.import_module(components_pkg)
    except ImportError as e:
        logger.warning(f"无法 import 阶段 {phase} 的组件包 {components_pkg}: {e}；跳过组件配置模板生成")
        return {}
    except Exception as e:
        logger.warning(f"加载阶段 {phase} 组件时出错: {e}；跳过组件配置模板生成")
        return {}

    registry_path = _PHASE_TO_REGISTRY.get((phase, registry_attr))
    if registry_path is None:
        return {}

    try:
        registry_module = importlib.import_module(registry_path)
        registry: dict[str, type] = getattr(registry_module, registry_attr, {})
    except ImportError as e:
        logger.warning(f"无法 import registry {registry_path}: {e}")
        return {}

    discovered: dict[str, type] = {}
    for name, cls in registry.items():
        config_schema = getattr(cls, "ConfigSchema", None)
        if config_schema is not None and isinstance(config_schema, type):
            discovered[name] = config_schema

    return discovered


def _generate_phase_toml(phase: str) -> str:
    """生成阶段配置文件（input.toml / decision.toml / output.toml）

    文件结构：
    1. 顶部骨架（启用列表等，按阶段硬编码）
    2. 自动发现的各组件配置模板（从 ConfigSchema 推导，含必填标记）
    """
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
        table.add(tomlkit.comment('  "command",  # 通用命令意图路由'))
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

    else:
        raise ValueError(f"未知阶段: {phase}")

    components = _discover_components(phase)
    section_key = _PHASE_TO_SECTION.get(phase)
    if components and section_key:
        parent_table = cast(Any, doc[section_key])
        parent_table.add(tomlkit.nl())
        parent_table.add(tomlkit.comment("=" * 60))
        parent_table.add(tomlkit.comment("以下为各组件配置模板（首次自动生成）"))
        parent_table.add(tomlkit.comment("启用组件步骤：1) 将组件名加到上方 enabled 列表  2) 填写 [必填] 字段"))
        parent_table.add(tomlkit.comment("=" * 60))
        parent_table.add(tomlkit.nl())

        for name in sorted(components.keys()):
            schema_cls = components[name]
            try:
                comp_table = _schema_to_toml_table(schema_cls)
                parent_table.add(tomlkit.comment(f"--- {name} ---"))
                if schema_cls.__doc__:
                    parent_table.add(tomlkit.comment(schema_cls.__doc__.strip().split("\n")[0]))
                parent_table[name] = comp_table
                parent_table.add(tomlkit.nl())
            except Exception as e:
                logger.warning(f"生成组件 {name} 配置模板失败: {e}，跳过")

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
