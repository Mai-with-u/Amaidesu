"""多文件配置加载器

从 config/ 目录加载 7 个 TOML 配置文件，使用 Pydantic Schema 验证并检测漂移。
首次运行时从 Schema 默认值生成带注释的配置文件。

配置文件结构（按域划分）:
    config/core.toml         - 基础设施（meta/general/persona/context/events/logging/dashboard/simulator/pipelines/interceptors）
    config/model.toml        - LLM/VLM 模型配置（[[llm_providers]] + [llm]/[llm_fast]/[vlm]/[llm_local]/[llm_summary]/[llm_agenda]）
    config/agents.toml       - 业务 Agent
    config/tools.toml        - 工具包启用/配置
    config/memory.toml       - 记忆系统（backend 行切换，db_path 权威在 storage.toml）
    config/storage.toml      - 存储（SQLite 路径，与 SimpleMemory 共用）
    config/background.toml   - 后台维护（ticks/压缩）

> **AGENTS.md 写回闭环**：所有 7 个文件均接入 ``_load_and_validate_schema``
> + ``_write_back_schema_file``，漂移字段会自动写回用户文件（缺失补默认、
> 冗余剥离、版本号更新）。未接入的文件其 Schema 变更永远不会写回用户文件。
"""

from __future__ import annotations

import importlib
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal, Optional, Union, cast, get_args, get_origin

import tomlkit
from tomlkit.items import AoT

from src.modules.config.schemas.base import BaseConfig, DriftReport, _set_toml_value
from src.modules.config.core_schemas import CoreConfig
from src.modules.config.model_schemas import ModelConfig
from src.modules.config.agents_schemas import AgentsRootConfig
from src.modules.config.tools_schemas import ToolsRootConfig
from src.modules.config.memory_schemas import MemoryRootConfig
from src.modules.config.storage_schemas import StorageRootConfig
from src.modules.config.background_schemas import BackgroundRootConfig
from src.modules.config.upgrade_hooks import apply_upgrade_hooks
from src.modules.logging import get_logger
from src.modules.simulator.config_schema import SimulatorConfigSchema
from pydantic import BaseModel

logger = get_logger("MultiFileLoader")

# 阶段→组件包/Registry 映射（迁移期辅助函数使用，load_config_dir 不依赖）
_PHASE_TO_SECTION: dict[str, str] = {
    "input": "collectors",
    "decision": "deciders",
    "output": "handlers",
}
_PHASE_TO_COMPONENTS_PKG: dict[str, str] = {
    "input": "src.stages.input.collectors",
    "decision": "src.stages.decision.deciders",
    # output handlers 已迁移到 src.modules.tools.output/
    "output": "src.modules.tools.output",
}
_PHASE_TO_REGISTRY: dict[tuple[str, str], str] = {
    ("input", "_COLLECTORS"): "src.stages.input.registry",
    ("decision", "_DECIDERS"): "src.stages.decision.registry",
    ("output", "_HANDLERS"): "src.stages.output.registry",
}

# 配置版本号。权威定义：本文件的 ``CONFIG_VERSION`` 与 ``MetaConfig.version``
# 默认值必须同步修改（改一必改二）。详见 AGENTS.md "配置 Schema 变更规则"。
CONFIG_VERSION = "2.0.12"

# 配置文件清单（按域划分）：core / model / agents / tools / memory / storage / background
_CONFIG_FILES = [
    "core.toml",
    "model.toml",
    "agents.toml",
    "tools.toml",
    "memory.toml",
    "storage.toml",
    "background.toml",
]


@dataclass(frozen=True)
class CrossFileMigration:
    """跨文件配置迁移：把源文件中的某个段合并到目标文件（一次性）。

    用于配置段跨文件移动（如旧 input.toml/decision.toml/output.toml 的
    [collectors]/[deciders]/[handlers] → 新 tools.toml/agents.toml 的对应段）。
    与 ``ConfigUpgradeHook`` 的区别：hook 只操作单文件 dict，本机制跨文件。
    """

    source_file: str
    source_key: str
    target_file: str
    target_key: str
    target_schema: type[BaseConfig] | None = None
    """目标段字段的 Schema 类；提供时迁移值会先清洗（剥离已删除的旧字段）再合并"""
    source_nested_path: tuple[str, ...] | None = None
    """单值移动模式的源下钻路径；从 source_key 段继续下钻取单值；同时需提供 target_nested_path"""
    target_nested_path: tuple[str, ...] | None = None
    """单值移动模式的目标下钻路径；与 source_nested_path 一一对应"""


# 已完成的跨文件迁移注册表（按时间顺序追加）
CROSS_FILE_MIGRATIONS: tuple[CrossFileMigration, ...] = (
    # simulator.toml 独立文件合并进 core.toml 的 [simulator]
    CrossFileMigration(
        source_file="simulator.toml",
        source_key="simulator",
        target_file="core.toml",
        target_key="simulator",
        target_schema=SimulatorConfigSchema,
    ),
    # input.toml → tools.toml 的 [tools.perception.config]
    CrossFileMigration(
        source_file="input.toml",
        source_key="collectors",
        target_file="tools.toml",
        target_key="perception_config",  # 注入到 [tools.perception.config] 字典
    ),
    # output.toml → tools.toml 的 [tools.output.config]
    CrossFileMigration(
        source_file="output.toml",
        source_key="handlers",
        target_file="tools.toml",
        target_key="output_config",
    ),
    # decision.toml → agents.toml 的 [agents] 段
    CrossFileMigration(
        source_file="decision.toml",
        source_key="deciders",
        target_file="agents.toml",
        target_key="agents",
    ),
    # TTS 基础设施重塑：tools.toml [tools.output.config].render_timeout_ms
    # 上移至 core.toml [tts].render_timeout_ms（保留用户原值）
    CrossFileMigration(
        source_file="tools.toml",
        source_key="tools",
        target_file="core.toml",
        target_key="tts",
        source_nested_path=("output", "config", "render_timeout_ms"),
        target_nested_path=("render_timeout_ms",),
    ),
    # TTS 彻底基础模块化：tools.toml 中四个引擎连接/合成子段整体迁入
    # core.toml [tts.<engine>]，用户原值完整搬运；源子段从 tools.toml 删除
    # 但 tools.toml 文件本身保留（TTS 之外的工具仍在其中）。
    CrossFileMigration(
        source_file="tools.toml",
        source_key="tools",
        target_file="core.toml",
        target_key="tts",
        source_nested_path=("output", "config", "edge_tts"),
        target_nested_path=("edge_tts",),
    ),
    CrossFileMigration(
        source_file="tools.toml",
        source_key="tools",
        target_file="core.toml",
        target_key="tts",
        source_nested_path=("output", "config", "gptsovits"),
        target_nested_path=("gptsovits",),
    ),
    CrossFileMigration(
        source_file="tools.toml",
        source_key="tools",
        target_file="core.toml",
        target_key="tts",
        source_nested_path=("output", "config", "voicebox"),
        target_nested_path=("voicebox",),
    ),
    CrossFileMigration(
        source_file="tools.toml",
        source_key="tools",
        target_file="core.toml",
        target_key="tts",
        source_nested_path=("output", "config", "omni_tts"),
        target_nested_path=("omni_tts",),
    ),
)


def _backup_file(file_path: Path, config_dir: Path, batch_id: str | None = None) -> Path | None:
    """备份配置文件到 config/old/ 目录

    Args:
        file_path: 源文件
        config_dir: config/ 目录
        batch_id: 批次目录名（同一批升级的文件共享同一目录，保留原文件名）；
            为 None 时保持旧行为（文件名加时间戳后缀）
    """
    if not file_path.exists():
        return None
    old_dir = config_dir / "old"
    if batch_id:
        old_dir = old_dir / batch_id
    old_dir.mkdir(parents=True, exist_ok=True)
    if batch_id:
        backup_name = f"{file_path.stem}{file_path.suffix}"
    else:
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
    doc.add(tomlkit.nl())

    for field_name, _field_info in CoreConfig.model_fields.items():
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

    def _populate_nested_table(table: Any, item: BaseModel) -> None:
        """把 BaseModel 字段填入 tomlkit table;None 值跳过 (留给运行时默认)。"""
        nested_cls = type(item)
        sub_config = item.model_dump()
        for sub_name, sub_info in nested_cls.model_fields.items():
            value = sub_config.get(sub_name)
            if value is None:
                continue
            if sub_info.description:
                table.add(tomlkit.comment(sub_info.description))
            table[sub_name] = value

    for field_name, field_info in ModelConfig.model_fields.items():
        field_value = getattr(model, field_name)

        if isinstance(field_value, BaseModel):
            sub_table = tomlkit.table()
            _populate_nested_table(sub_table, field_value)

            if field_info.description:
                doc.add(tomlkit.comment(field_info.description))
            doc[field_name] = sub_table
            doc.add(tomlkit.nl())
        elif isinstance(field_value, list) and field_value and isinstance(field_value[0], BaseModel):
            # list[BaseModel] → TOML array-of-tables (`[[field_name]]`)
            if field_info.description:
                doc.add(tomlkit.comment(field_info.description))

            tables = []
            for item in field_value:
                item_table = tomlkit.table()
                _populate_nested_table(item_table, item)
                tables.append(item_table)
            doc[field_name] = AoT(tables)
            doc.add(tomlkit.nl())
        else:
            if field_info.description:
                doc.add(tomlkit.comment(field_info.description))
            if field_value is None:
                continue
            doc[field_name] = field_value
            doc.add(tomlkit.nl())

    return tomlkit.dumps(doc)


def _generate_domain_toml(
    file_name: str,
    schema_cls: type[BaseConfig],
    *,
    comment: str | None = None,
) -> str:
    """通用域文件生成器（agents/tools/memory/storage/background 共用）

    与 ``_generate_core_toml`` 风格一致：顶层字段即顶层表，BaseModel 字段展开为子表。
    """
    doc = tomlkit.document()
    doc.add(tomlkit.comment(comment or f"{file_name} 配置 - Amaidesu"))
    doc.add(tomlkit.nl())

    instance = schema_cls()

    for field_name, _field_info in schema_cls.model_fields.items():
        field_value = getattr(instance, field_name)

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
        else:
            if _field_info.description:
                doc.add(tomlkit.comment(_field_info.description))
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


def _placeholder_for_type(annotation: Any, field_info: Any = None) -> Any:
    """根据字段注解生成占位符值。"""
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
        for meta in getattr(field_info, "metadata", []) or []:
            gt = getattr(meta, "gt", None)
            if gt is not None and gt >= 0:
                return int(gt) + 1
            ge = getattr(meta, "ge", None)
            if ge is not None and ge > 0:
                return int(ge)
        return 0
    if annotation is float:
        for meta in getattr(field_info, "metadata", []) or []:
            gt = getattr(meta, "gt", None)
            if gt is not None and gt >= 0:
                return float(gt) + 1.0
            ge = getattr(meta, "ge", None)
            if ge is not None and ge > 0:
                return float(ge)
        return 0.0
    if annotation is bool:
        return False
    if annotation is bytes:
        return b""
    return ""


def _extract_constraint_hints(field_info: Any) -> str:
    """从 Pydantic v2 Field 的 metadata 中提取 gt/ge/lt/le/length 等约束提示。"""
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

    保留迁移期能力，但当前已不依赖此函数（每个域用 _generate_domain_toml）。
    """
    table = tomlkit.table()

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
            if isinstance(default_value, dict) and default_value:
                is_subtable = True

        target = subtable_fields if is_subtable else simple_fields
        target.append((field_name, field_info, unwrapped))

    # 第一遍：输出所有简单字段
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
            table[field_name] = _placeholder_for_type(unwrapped, field_info)
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

    # 第二遍：输出所有子表字段
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
            sub_table = tomlkit.table()
            for sub_key, sub_value in default_value.items():
                if isinstance(sub_value, dict):
                    inner_table = tomlkit.table()
                    for inner_key, inner_value in sub_value.items():
                        inner_table[inner_key] = inner_value
                    sub_table[sub_key] = inner_table
                else:
                    sub_table[sub_key] = sub_value

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
    """发现某阶段所有已注册组件的 ConfigSchema 类（迁移期保留）。"""
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

    仍为旧 input/decision/output 文件生成骨架，供 stages/
    业务代码过渡使用。新配置写入由 CrossFileMigration 引导到 tools.toml /
    agents.toml，源文件被备份后移除。
    """
    doc = tomlkit.document()

    if phase == "input":
        doc.add(tomlkit.comment("Input 阶段配置（兼容期：下次启动将被迁移到 tools.toml [tools.perception]）"))
        doc.add(tomlkit.nl())
        table = tomlkit.table()
        table.add(tomlkit.comment("启用的 Collector 列表"))
        table["enabled"] = []
        doc["collectors"] = table

    elif phase == "decision":
        doc.add(tomlkit.comment("Decision 阶段配置（兼容期：下次启动将被迁移到 agents.toml [agents]）"))
        doc.add(tomlkit.nl())
        table = tomlkit.table()
        table.add(tomlkit.comment("启用的 Decider 列表"))
        table["enabled"] = ["amaidesu"]
        doc["deciders"] = table

    elif phase == "output":
        doc.add(tomlkit.comment("Output 阶段配置（兼容期：下次启动将被迁移到 tools.toml [tools.output]）"))
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


def _table_from_model(instance: BaseModel) -> Any:
    """把 BaseModel 实例序列化为 tomlkit Table（值 + description 注释，None 跳过）。

    dict 值（free-form 子段）不加容器级 description 注释——注释插在父表流
    会与子表表头错位，且破坏按表头定位键值的文本消费者；子段内字段的
    注释由字段级 description 在具体 Schema 序列化路径中提供。
    """
    table = tomlkit.table()
    for sub_name, sub_info in type(instance).model_fields.items():
        value = getattr(instance, sub_name)
        if value is None:
            continue
        if isinstance(value, BaseModel):
            inner = _table_from_model(value)
        elif isinstance(value, list) and value and all(isinstance(v, BaseModel) for v in value):
            inner = tomlkit.aot()
            for item in value:
                inner.append(_table_from_model(item))
        elif isinstance(value, dict):
            inner = _dict_to_toml_table(value)
        else:
            inner = value
        if sub_info.description:
            table.add(tomlkit.comment(sub_info.description))
        table[sub_name] = inner
    return table


def _dict_to_toml_table(data: dict[str, Any]) -> Any:
    """把嵌套 dict 转为 tomlkit Table。"""
    table = tomlkit.table()
    for key, value in data.items():
        if isinstance(value, dict):
            table[key] = _dict_to_toml_table(value)
        else:
            table[key] = value
    return table


def _serialize_instance_to_toml(
    schema_cls: type[BaseModel],
    instance: BaseModel,
    *,
    compact: bool = False,
) -> str:
    """把配置实例序列化为多文件格式 TOML（顶层字段即顶层表/键值）。

    Args:
        compact: 紧凑模式——字段之间不插入空行（供子段补全生成使用，
            使补全段与用户手写风格一致）；整文件写回保持默认的宽松排版。
    """
    doc = tomlkit.document()
    for field_name, field_info in schema_cls.model_fields.items():
        value = getattr(instance, field_name)
        if value is None:
            continue
        if isinstance(value, BaseModel):
            table = _table_from_model(value)
        elif isinstance(value, list) and value and all(isinstance(v, BaseModel) for v in value):
            table = tomlkit.aot()
            for item in value:
                table.append(_table_from_model(item))
        elif isinstance(value, dict):
            # dict 子段（free-form）不加容器级注释：文本稳定性优先；
            # 字段级注释由各 Provider Schema 的字段 description 在补全/写回路径提供
            table = _dict_to_toml_table(value)
        else:
            if field_info.description:
                doc.add(tomlkit.comment(field_info.description))
            doc[field_name] = value
            if not compact:
                doc.add(tomlkit.nl())
            continue
        if field_info.description and not isinstance(value, dict):
            doc.add(tomlkit.comment(field_info.description))
        doc[field_name] = table
        if not compact:
            doc.add(tomlkit.nl())
    return tomlkit.dumps(doc)


# 子段名 → ConfigSchema 延迟加载器，按配置宿主分两组：
# - TTS 引擎是基础模块，配置宿主为 core.toml [tts]
# - 其余渲染工具仍是 Agent 工具，配置宿主为 tools.toml [tools.output.config]
# Schema 嵌在 Provider 类内部且 Provider 模块 import 较重（audio/网络依赖），
# 因此仅在补全流程实际运行时才加载；加载失败（依赖缺失）时跳过该子段。
_TTS_ENGINE_SCHEMA_LOADERS: dict[str, Callable[[], Optional[type[BaseModel]]]] = {
    "edge_tts": lambda: _try_import_provider_schema("src.modules.tts.edge_tts_tool", "EdgeTTSProvider"),
    "gptsovits": lambda: _try_import_provider_schema("src.modules.tts.gptsovits_tool", "GPTSoVITSProvider"),
    "omni_tts": lambda: _try_import_provider_schema("src.modules.tts.omni_tts_tool", "OmniTTSProvider"),
    "voicebox": lambda: _try_import_provider_schema("src.modules.tts.voicebox_tool", "VoiceboxProvider"),
}

_TOOL_PROVIDER_SCHEMA_LOADERS: dict[str, Callable[[], Optional[type[BaseModel]]]] = {
    "subtitle": lambda: _try_import_provider_schema(
        "src.modules.tools.output.subtitle.subtitle_service", "SubtitleGuiService"
    ),
    "vts": lambda: _try_import_provider_schema("src.modules.tools.output.vts.vts_provider", "VTSProvider"),
    "vrchat": lambda: _try_import_provider_schema("src.modules.tools.output.vts.vrchat_provider", "VRChatProvider"),
    "warudo": lambda: _try_import_provider_schema("src.modules.tools.output.warudo.warudo_provider", "WarudoProvider"),
    "obs": lambda: _try_import_provider_schema("src.modules.tools.output.obs.obs_provider", "OBSProvider"),
}


def _try_import_provider_schema(module_path: str, provider_name: str) -> Optional[type[BaseModel]]:
    """延迟加载 Provider 模块并返回其 ConfigSchema；失败返回 None。"""
    try:
        import importlib

        module = importlib.import_module(module_path)
        cls = getattr(module, provider_name, None)
        if cls is None:
            return None
        return getattr(cls, "ConfigSchema", None)
    except Exception:
        return None


def _complete_provider_config_sections(
    config_dir: Path,
    section_container: dict[str, Any],
    table_prefix: str,
    schema_loaders: dict[str, Callable[[], Optional[type[BaseModel]]]],
    file_name: str,
    batch_id: str | None = None,
) -> list[str]:
    """Provider 子配置补全：``[<table_prefix>.<name>]`` 缺失键由 Provider
    ConfigSchema 默认值补齐，并以 Schema 注释格式重写该子段（用户已填值保留）。

    Provider 子段是 free-form dict，不参与宿主文件根 Schema 校验；Provider 新增
    配置字段时用户文件无法感知。本函数在宿主文件加载后运行，把"模板滞后"
    的字段补进用户文件，使配置文件始终自描述（字段 + 注释说明）。

    Args:
        config_dir: 配置目录（备份与读写宿主文件）。
        section_container: 宿主文件中承载各子段的容器 dict（如 core.toml 的
            ``[tts]`` 段或 tools.toml 的 ``[tools.output.config]`` 段内存表示），
            补全结果同步回写。
        table_prefix: 子段表头前缀（``"tts"`` 或 ``"tools.output.config"``）。
        schema_loaders: 子段名 → ConfigSchema 延迟加载器。
        file_name: 宿主文件名（``"core.toml"`` / ``"tools.toml"``）。
        batch_id: 备份批次号。

    Returns:
        被补全的子段名列表（如 ``["gptsovits"]``）。
    """
    file_path = config_dir / file_name
    if not file_path.exists():
        return []

    try:
        text = file_path.read_text(encoding="utf-8-sig")
    except OSError:
        return []

    completed: list[str] = []
    for key, schema_loader in schema_loaders.items():
        user_data = section_container.get(key)
        if not isinstance(user_data, dict):
            continue
        schema_cls = schema_loader()
        if schema_cls is None:
            continue
        defaults = schema_cls().model_dump()
        missing_keys = [k for k in defaults if k not in user_data]
        if not missing_keys:
            continue

        merged = {**defaults, **user_data}
        instance = schema_cls(**merged)
        body = _serialize_instance_to_toml(schema_cls, instance, compact=True)
        new_section = f"[{table_prefix}.{key}]\n{body}"

        # 文本级整段替换：从子段表头到下一个表头（或文件尾）
        pattern = re.compile(rf"(?ms)^\[{re.escape(table_prefix)}\.{re.escape(key)}\][ \t]*\r?\n.*?(?=^\[|\Z)")
        if not pattern.search(text):
            # 用户文件中该子段整段缺失：追加到容器表头之后
            header_pattern = re.compile(rf"(?m)^(\[{re.escape(table_prefix)}\][ \t]*\r?\n)")
            if header_pattern.search(text):
                text = header_pattern.sub(lambda m, s=new_section: m.group(1) + s + "\n", text, count=1)
            else:
                text = text.rstrip("\n") + "\n\n" + new_section
        else:
            text = pattern.sub(lambda _m, s=new_section: s, text)

        # 同步内存中的容器，保持文件与本次加载结果一致
        section_container[key] = merged
        completed.append(key)
        logger.info(f"{file_name} 子段 '{key}' 补全 {len(missing_keys)} 个缺失配置项（含注释说明）")

    if completed:
        try:
            _backup_file(file_path, config_dir, batch_id=batch_id)
            file_path.write_text(text, encoding="utf-8")
        except OSError as e:
            logger.warning(f"{file_name} 子段补全写回失败（内存中已补齐）: {e}")
    return completed


def _write_back_schema_file(
    config_dir: Path,
    file_name: str,
    schema_cls: type[BaseModel],
    user_data: dict[str, Any],
    *,
    force_meta_version: str | None = None,
    batch_id: str | None = None,
) -> Path | None:
    """自动升级写回：备份旧文件 → 用户值合并（缺失补默认、冗余已剥离）→ 序列化写回。"""
    file_path = config_dir / file_name
    data = dict(user_data)
    if force_meta_version is not None:
        meta = dict(data.get("meta", {}))
        meta["version"] = force_meta_version
        data["meta"] = meta

    instance = schema_cls(**data)
    content = _serialize_instance_to_toml(schema_cls, instance)
    backup_path = _backup_file(file_path, config_dir, batch_id=batch_id)
    has_bom = False
    try:
        with open(file_path, "rb") as f:
            has_bom = f.read(3) == b"\xef\xbb\xbf"
    except OSError:
        pass
    encoding = "utf-8-sig" if has_bom else "utf-8"
    file_path.write_text(content, encoding=encoding)
    return backup_path


def _ensure_required_files(config_dir: Path) -> list[str]:
    """补齐缺失的必需配置文件（7 个域文件）。

    Returns:
        本次补齐的文件名列表
    """
    generated: list[str] = []
    for fname in _CONFIG_FILES:
        if (config_dir / fname).exists():
            continue
        logger.info(f"缺失配置文件 {fname}，自动生成...")
        if fname == "core.toml":
            (config_dir / fname).write_text(_generate_core_toml(), encoding="utf-8-sig")
        elif fname == "model.toml":
            (config_dir / fname).write_text(_generate_model_toml(), encoding="utf-8-sig")
        elif fname == "agents.toml":
            (config_dir / fname).write_text(
                _generate_domain_toml(fname, AgentsRootConfig, comment="业务 Agent 配置"),
                encoding="utf-8-sig",
            )
        elif fname == "tools.toml":
            (config_dir / fname).write_text(
                _generate_domain_toml(fname, ToolsRootConfig, comment="工具包配置"),
                encoding="utf-8-sig",
            )
        elif fname == "memory.toml":
            (config_dir / fname).write_text(
                _generate_domain_toml(fname, MemoryRootConfig, comment="记忆系统配置"),
                encoding="utf-8-sig",
            )
        elif fname == "storage.toml":
            (config_dir / fname).write_text(
                _generate_domain_toml(fname, StorageRootConfig, comment="存储配置（SQLite 路径单一权威）"),
                encoding="utf-8-sig",
            )
        elif fname == "background.toml":
            (config_dir / fname).write_text(
                _generate_domain_toml(fname, BackgroundRootConfig, comment="后台维护配置"),
                encoding="utf-8-sig",
            )
        generated.append(fname)
    return generated


def generate_default_configs(config_dir: Path) -> None:
    """首次运行：从 Schema 生成默认配置文件（7 个域文件）"""
    config_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"生成默认配置到 {config_dir}")

    for fname, schema_cls, comment in (
        ("core.toml", CoreConfig, "核心系统配置"),
        ("model.toml", ModelConfig, "模型配置 - LLM/VLM 参数"),
        ("agents.toml", AgentsRootConfig, "业务 Agent 配置"),
        ("tools.toml", ToolsRootConfig, "工具包配置"),
        ("memory.toml", MemoryRootConfig, "记忆系统配置"),
        ("storage.toml", StorageRootConfig, "存储配置（SQLite 路径单一权威）"),
        ("background.toml", BackgroundRootConfig, "后台维护配置"),
    ):
        path = config_dir / fname
        if fname == "core.toml":
            content = _generate_core_toml()
        elif fname == "model.toml":
            content = _generate_model_toml()
        else:
            content = _generate_domain_toml(fname, schema_cls, comment=comment)
        path.write_text(content, encoding="utf-8-sig")
        logger.info(f"已生成 {fname}")


def _collect_empty_container_fields(schema_cls: type) -> set[str]:
    """收集 schema 中"默认是空容器"的字段名集合。"""
    result: set[str] = set()
    for field_name, field_info in getattr(schema_cls, "model_fields", {}).items():
        try:
            default_value = field_info.get_default(call_default_factory=True)
        except Exception:
            default_value = None
        if isinstance(default_value, BaseConfig):
            if not default_value.model_dump():
                result.add(field_name)
            result |= _collect_empty_container_fields(type(default_value))
        elif isinstance(default_value, (dict, list)) and not default_value:
            result.add(field_name)
    return result


def _filter_optional_container_missing(report: DriftReport, schema_cls: type[BaseConfig]) -> None:
    """从漂移报告中剔除"缺失但默认是空容器"的字段（原地修改 missing）。"""
    empty_fields = _collect_empty_container_fields(schema_cls)
    report.missing = [m for m in report.missing if m.split(".")[-1] not in empty_fields]


def _apply_file_upgrade_hooks(
    config_dir: Path,
    file_name: str,
    schema_cls: type[BaseModel],
    raw_data: dict[str, Any],
    current_ver: str | None,
) -> None:
    """对单个配置文件执行版本升级钩子并尝试写回。

    钩子幂等可重复执行；迁移成功后用写回闭环持久化修复结果，
    使后续 ``_load_and_validate_schema`` 从已修复的磁盘数据校验。
    写回失败（数据仍不合法）时保留原配置，由调用方回退 raw dict。
    """
    hook_result = apply_upgrade_hooks(raw_data, file_name, current_ver or CONFIG_VERSION, CONFIG_VERSION)
    if hook_result.migrated:
        logger.warning(f"{file_name} 配置升级钩子已应用: {hook_result.reasons}")
        try:
            _write_back_schema_file(
                config_dir,
                file_name,
                schema_cls,
                hook_result.data,
                batch_id=datetime.now().strftime("%Y%m%d_%H%M%S"),
            )
            logger.info(f"{file_name} 升级钩子修复已写回")
        except Exception as e:
            logger.warning(f"{file_name} 升级钩子写回失败，保留原配置: {e}")


def _load_and_validate_schema(
    file_path: Path,
    schema_cls: type[BaseConfig],
) -> tuple[dict[str, Any], DriftReport]:
    """加载单个 Schema 配置文件并验证。"""
    with open(file_path, "r", encoding="utf-8-sig") as f:
        doc = tomlkit.load(f)

    raw_data = doc.unwrap()
    instance, report = schema_cls.from_dict_with_drift_check(raw_data)
    return instance.model_dump(), report


def _apply_cross_file_migrations(
    config_dir: Path,
    target_file_data: dict[str, dict[str, Any]],
    target_files: list[str],
) -> tuple[list[Path], set[str]]:
    """执行已注册的跨文件迁移。

    Args:
        config_dir: config/ 目录
        target_file_data: 已加载的目标文件数据（in-out 修改，如 tools_data）
        target_files: 参与迁移的目标文件列表（按文件名）

    Returns:
        ``(to_remove, modified_targets)``：
        - ``to_remove``：需要备份后移除的源文件路径列表（旧阶段文件整体淘汰）
        - ``modified_targets``：被本次迁移修改过的目标文件名集合，用于触发目标
          文件的写回闭环（迁移虽未漂移字段，但内存字典已被改动，落盘才能持久化）
    """
    to_remove: list[Path] = []
    modified_targets: set[str] = set()
    for migration in CROSS_FILE_MIGRATIONS:
        source_path = config_dir / migration.source_file
        if not source_path.exists():
            continue
        if migration.target_file not in target_files:
            # 目标文件未在本次加载列表中，跳过（用户可能尚未升级到该域）
            continue

        target_raw = target_file_data.get(migration.target_file, {})
        with open(source_path, "r", encoding="utf-8-sig") as f:
            source_doc = tomlkit.load(f).unwrap()

        # 单值深嵌套移动模式（见 CrossFileMigration 文档）
        if migration.source_nested_path is not None and migration.target_nested_path is not None:
            source_path_keys = (migration.source_key, *migration.source_nested_path)
            target_path_keys = (migration.target_key, *migration.target_nested_path)
            source_value = _drill_get(source_doc, source_path_keys)
            if source_value is None:
                continue
            _drill_set(target_raw, target_path_keys, source_value)
            _drill_del(source_doc, source_path_keys)
            _write_toml_preserve_bom(source_path, tomlkit.dumps(source_doc))
            modified_targets.add(migration.target_file)
            logger.info(
                f"跨文件迁移(单值): [{migration.source_file}]."
                f"{'.'.join(source_path_keys)} → [{migration.target_file}].{'.'.join(target_path_keys)}"
            )
            continue

        section = source_doc.get(migration.source_key)
        if not isinstance(section, dict):
            continue

        if migration.target_schema is not None:
            try:
                cleaned_instance, _ = migration.target_schema.from_dict_with_drift_check(section)
                section = cleaned_instance.model_dump()
            except Exception as e:
                logger.warning(f"跨文件迁移 [{migration.source_file}] 数据清洗失败，跳过合并: {e}")
                section = None

        if section is None:
            continue

        migrated = False
        if migration.target_key in ("perception_config", "output_config"):
            pack_name = migration.target_key.split("_")[0]
            tools_dict = target_raw.setdefault("tools", {})
            pack_dict = tools_dict.setdefault(pack_name, {})
            if not isinstance(pack_dict, dict):
                pack_dict = {}
                tools_dict[pack_name] = pack_dict
            meta_dict = pack_dict.setdefault("config", {})
            if isinstance(meta_dict, dict):
                meta_dict.update(section)
                migrated = bool(section)
            else:
                pack_dict["config"] = section
                migrated = True
        else:
            if migration.target_key not in target_raw:
                target_raw[migration.target_key] = section
                migrated = True
            elif isinstance(target_raw[migration.target_key], dict):
                target_raw[migration.target_key].update(section)
                migrated = bool(section)

        if not migrated:
            continue

        modified_targets.add(migration.target_file)
        logger.info(
            f"跨文件迁移: [{migration.source_file}].{migration.source_key} "
            f"→ [{migration.target_file}].{migration.target_key}"
        )
        to_remove.append(source_path)
    return to_remove, modified_targets


def _drill_get(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    """沿 path 逐层下钻 dict 取值；任一层缺失或非 dict 返回 None。"""
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        if key not in current:
            return None
        current = current[key]
    return current


def _drill_set(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    """沿 path 逐层下钻设置值；中间缺失的 dict 自动创建（原地修改）。"""
    current = data
    for key in path[:-1]:
        next_val = current.get(key)
        if not isinstance(next_val, dict):
            next_val = {}
            current[key] = next_val
        current = next_val
    current[path[-1]] = value


def _drill_del(data: dict[str, Any], path: tuple[str, ...]) -> None:
    """沿 path 逐层下钻删除末键；路径不完整时静默 no-op（原地修改）。"""
    current = data
    for key in path[:-1]:
        if not isinstance(current, dict):
            return
        if key not in current:
            return
        current = current[key]
    if isinstance(current, dict):
        current.pop(path[-1], None)


def _write_toml_preserve_bom(file_path: Path, content: str) -> None:
    """写回 TOML 文件，保留原有 BOM（utf-8-sig 格式）。"""
    has_bom = False
    try:
        with open(file_path, "rb") as f:
            has_bom = f.read(3) == b"\xef\xbb\xbf"
    except OSError:
        pass
    encoding = "utf-8-sig" if has_bom else "utf-8"
    file_path.write_text(content, encoding=encoding)


def _log_drift_writeback(
    file_label: str,
    backup_path: Path | None,
    config_dir: Path,
    missing: list[str],
    redundant: list[str],
    old_version: str | None,
) -> None:
    """统一的"已自动升级"日志格式（所有 7 个文件共用）。"""
    backup_rel = f", 备份: {backup_path.relative_to(config_dir)}" if backup_path else ""
    version_info = (
        f", 版本 {old_version or '?'} → {CONFIG_VERSION}" if old_version and old_version != CONFIG_VERSION else ""
    )
    logger.info(
        f"{file_label} 已自动升级: "
        f"补齐 {len(missing)} 项({', '.join(missing) or '无'}), "
        f"清理 {len(redundant)} 项({', '.join(redundant) or '无'})"
        f"{version_info}{backup_rel}"
    )


def load_config_dir(
    config_dir: Path,
) -> tuple[dict[str, Any], DriftReport]:
    """加载 config/ 目录下所有 7 个 TOML 配置文件（含自动升级闭环）

    Args:
        config_dir: config/ 目录路径

    Returns:
        (合并后的配置字典, 综合漂移报告)

    自动升级闭环（AGENTS.md 规则全覆盖 7 个文件）：
    1. 缺失文件自动补齐
    2. 跨文件迁移（如旧 input.toml → tools.toml 的 [tools.perception.config]）
    3. core.toml 版本不一致 → 执行注册的 ConfigUpgradeHook → 写回并更新 [meta].version
    4. 存在漂移（缺失/冗余字段）→ 备份 + 写回（缺失补默认值、冗余删除）
    写回后漂移归零，下次启动不再重复提示。
    """
    # 0. 补齐缺失文件（7 个域文件）
    _ensure_required_files(config_dir)

    combined = DriftReport()
    result: dict[str, Any] = {}
    current_ver = get_config_version(config_dir)
    version_changed = current_ver is not None and current_ver != CONFIG_VERSION
    batch_id: str | None = None

    # 收集所有 7 个文件的目标数据（用于跨文件迁移）
    loaded_data: dict[str, dict[str, Any]] = {}

    # ====== 1. core.toml ======
    core_path = config_dir / "core.toml"
    if core_path.exists():
        core_data, core_report = _load_and_validate_schema(core_path, CoreConfig)
        loaded_data["core.toml"] = core_data
        if version_changed:
            hook_result = apply_upgrade_hooks(core_data, "core.toml", current_ver or CONFIG_VERSION, CONFIG_VERSION)
            if hook_result.migrated:
                logger.warning(f"core.toml 配置升级钩子已应用: {hook_result.reasons}")
            core_data = hook_result.data
        # 跨文件迁移（如旧 simulator.toml → core 的 [simulator]）
        migrated_files, modified_targets = _apply_cross_file_migrations(
            config_dir, {"core.toml": core_data}, ["core.toml"]
        )
        if version_changed or core_report.has_drift or migrated_files or modified_targets:
            batch_id = batch_id or datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = _write_back_schema_file(
                config_dir, "core.toml", CoreConfig, core_data, force_meta_version=CONFIG_VERSION, batch_id=batch_id
            )
            _log_drift_writeback(
                "core.toml", backup, config_dir, core_report.missing, core_report.redundant, current_ver
            )
            for migrated_file in migrated_files:
                _backup_file(migrated_file, config_dir, batch_id=batch_id)
                migrated_file.unlink()
            core_data, core_report = _load_and_validate_schema(core_path, CoreConfig)
            _filter_optional_container_missing(core_report, CoreConfig)

        # TTS 引擎是基础模块，其参数子段（[tts.<engine>]）为 free-form dict，
        # 不参与 CoreConfig 校验；按引擎 ConfigSchema 补齐缺失键并以注释写回，
        # 使引擎参数在配置文件中自描述（否则新增字段用户无从得知）。
        tts_section = core_data.get("tts", {}) if isinstance(core_data, dict) else {}
        if isinstance(tts_section, dict):
            tts_completed = _complete_provider_config_sections(
                config_dir,
                tts_section,
                table_prefix="tts",
                schema_loaders=_TTS_ENGINE_SCHEMA_LOADERS,
                file_name="core.toml",
                batch_id=batch_id,
            )
            if tts_completed:
                # 补全改变了文件，重新加载以取到与磁盘一致的最新内容
                core_data, core_report = _load_and_validate_schema(core_path, CoreConfig)

        result["core"] = core_data
        combined.redundant.extend(f"core.{r}" for r in core_report.redundant)
        combined.missing.extend(f"core.{m}" for m in core_report.missing)

    # ====== 2. model.toml ======
    model_path = config_dir / "model.toml"
    if model_path.exists():
        try:
            with open(model_path, "r", encoding="utf-8-sig") as f:
                raw_model = tomlkit.load(f).unwrap()
            loaded_data["model.toml"] = raw_model
            _apply_file_upgrade_hooks(config_dir, "model.toml", ModelConfig, raw_model, current_ver)
            model_data, model_report = _load_and_validate_schema(model_path, ModelConfig)
        except Exception as e:
            logger.warning(f"model.toml Schema 验证失败，回退 raw dict 加载: {e}")
            with open(model_path, "r", encoding="utf-8-sig") as f:
                model_data = tomlkit.load(f).unwrap()
                model_report = DriftReport()
        loaded_data["model.toml"] = model_data
        if model_report.has_drift:
            try:
                batch_id = batch_id or datetime.now().strftime("%Y%m%d_%H%M%S")
                backup = _write_back_schema_file(config_dir, "model.toml", ModelConfig, model_data, batch_id=batch_id)
                _log_drift_writeback(
                    "model.toml", backup, config_dir, model_report.missing, model_report.redundant, current_ver
                )
            except Exception as e:
                logger.warning(f"model.toml 写回失败（验证仍不通过），跳过: {e}")
        result["model"] = model_data
        combined.redundant.extend(f"model.{r}" for r in model_report.redundant)
        combined.missing.extend(f"model.{m}" for m in model_report.missing)

    # ====== 3. agents.toml ======
    agents_path = config_dir / "agents.toml"
    if agents_path.exists():
        try:
            # 跨文件迁移（如旧 decision.toml 的 [deciders] → agents.toml 的 [agents]）
            # 先读取 agents.toml 原始数据，让跨文件迁移注入
            with open(agents_path, "r", encoding="utf-8-sig") as f:
                raw_agents = tomlkit.load(f).unwrap()
            loaded_data["agents.toml"] = raw_agents
            _apply_file_upgrade_hooks(config_dir, "agents.toml", AgentsRootConfig, raw_agents, current_ver)
            migrated_files, modified_targets = _apply_cross_file_migrations(
                config_dir, loaded_data, list(loaded_data.keys())
            )
            if migrated_files or modified_targets:
                # 写回含迁移数据的 agents.toml
                agents_path.write_text(tomlkit.dumps(raw_agents), encoding="utf-8")

            agents_data, agents_report = _load_and_validate_schema(agents_path, AgentsRootConfig)
            _filter_optional_container_missing(agents_report, AgentsRootConfig)
            if agents_report.has_drift or migrated_files or modified_targets:
                batch_id = batch_id or datetime.now().strftime("%Y%m%d_%H%M%S")
                backup = _write_back_schema_file(
                    config_dir, "agents.toml", AgentsRootConfig, agents_data, batch_id=batch_id
                )
                _log_drift_writeback(
                    "agents.toml", backup, config_dir, agents_report.missing, agents_report.redundant, current_ver
                )
                for migrated_file in migrated_files:
                    if migrated_file.exists():
                        _backup_file(migrated_file, config_dir, batch_id=batch_id)
                        migrated_file.unlink()
                agents_data, agents_report = _load_and_validate_schema(agents_path, AgentsRootConfig)
                _filter_optional_container_missing(agents_report, AgentsRootConfig)
            result["agents"] = agents_data
            combined.redundant.extend(f"agents.{r}" for r in agents_report.redundant)
            combined.missing.extend(f"agents.{m}" for m in agents_report.missing)
        except Exception as e:
            logger.warning(f"agents.toml Schema 验证失败，回退 raw dict 加载: {e}")
            with open(agents_path, "r", encoding="utf-8-sig") as f:
                result["agents"] = tomlkit.load(f).unwrap()

    # ====== 4. tools.toml ======
    tools_path = config_dir / "tools.toml"
    if tools_path.exists():
        try:
            with open(tools_path, "r", encoding="utf-8-sig") as f:
                raw_tools = tomlkit.load(f).unwrap()
            loaded_data["tools.toml"] = raw_tools
            _apply_file_upgrade_hooks(config_dir, "tools.toml", ToolsRootConfig, raw_tools, current_ver)
            # 跨文件迁移（如旧 input.toml/output.toml 的 [collectors]/[handlers] → tools.toml）
            migrated_files, modified_targets = _apply_cross_file_migrations(
                config_dir, loaded_data, list(loaded_data.keys())
            )
            if migrated_files or modified_targets:
                tools_path.write_text(tomlkit.dumps(raw_tools), encoding="utf-8")

            tools_data, tools_report = _load_and_validate_schema(tools_path, ToolsRootConfig)
            _filter_optional_container_missing(tools_report, ToolsRootConfig)
            if tools_report.has_drift or migrated_files or modified_targets:
                batch_id = batch_id or datetime.now().strftime("%Y%m%d_%H%M%S")
                backup = _write_back_schema_file(
                    config_dir, "tools.toml", ToolsRootConfig, tools_data, batch_id=batch_id
                )
                _log_drift_writeback(
                    "tools.toml", backup, config_dir, tools_report.missing, tools_report.redundant, current_ver
                )
                for migrated_file in migrated_files:
                    if migrated_file.exists():
                        _backup_file(migrated_file, config_dir, batch_id=batch_id)
                        migrated_file.unlink()
                tools_data, tools_report = _load_and_validate_schema(tools_path, ToolsRootConfig)
                _filter_optional_container_missing(tools_report, ToolsRootConfig)

            # Provider 子配置补全：引擎/皮套等子段为 free-form dict，不参与
            # ToolsRootConfig 校验；此处按各 Provider ConfigSchema 补齐缺失键
            # 并以注释格式写回，使配置文件自描述（否则新增字段用户无从得知）。
            tools_root = tools_data.get("tools", {}) if isinstance(tools_data, dict) else {}
            output_pack = tools_root.get("output", {}) if isinstance(tools_root, dict) else {}
            output_cfg = output_pack.get("config", {}) if isinstance(output_pack, dict) else {}
            if isinstance(output_cfg, dict):
                completed = _complete_provider_config_sections(
                    config_dir,
                    output_cfg,
                    table_prefix="tools.output.config",
                    schema_loaders=_TOOL_PROVIDER_SCHEMA_LOADERS,
                    file_name="tools.toml",
                    batch_id=batch_id,
                )
                if completed:
                    # 补全改变了文件，重新加载以取到与磁盘一致的最新内容
                    tools_data, tools_report = _load_and_validate_schema(tools_path, ToolsRootConfig)
                    _filter_optional_container_missing(tools_report, ToolsRootConfig)

            result["tools"] = tools_data
            combined.redundant.extend(f"tools.{r}" for r in tools_report.redundant)
            combined.missing.extend(f"tools.{m}" for m in tools_report.missing)
        except Exception as e:
            logger.warning(f"tools.toml Schema 验证失败，回退 raw dict 加载: {e}")
            with open(tools_path, "r", encoding="utf-8-sig") as f:
                result["tools"] = tomlkit.load(f).unwrap()

    # ====== 5. memory.toml ======
    memory_path = config_dir / "memory.toml"
    if memory_path.exists():
        try:
            with open(memory_path, "r", encoding="utf-8-sig") as f:
                raw_memory = tomlkit.load(f).unwrap()
            loaded_data["memory.toml"] = raw_memory
            _apply_file_upgrade_hooks(config_dir, "memory.toml", MemoryRootConfig, raw_memory, current_ver)
            memory_data, memory_report = _load_and_validate_schema(memory_path, MemoryRootConfig)
            _filter_optional_container_missing(memory_report, MemoryRootConfig)
            if memory_report.has_drift:
                batch_id = batch_id or datetime.now().strftime("%Y%m%d_%H%M%S")
                backup = _write_back_schema_file(
                    config_dir, "memory.toml", MemoryRootConfig, memory_data, batch_id=batch_id
                )
                _log_drift_writeback(
                    "memory.toml", backup, config_dir, memory_report.missing, memory_report.redundant, current_ver
                )
                memory_data, memory_report = _load_and_validate_schema(memory_path, MemoryRootConfig)
                _filter_optional_container_missing(memory_report, MemoryRootConfig)
            result["memory"] = memory_data
            combined.redundant.extend(f"memory.{r}" for r in memory_report.redundant)
            combined.missing.extend(f"memory.{m}" for m in memory_report.missing)
        except Exception as e:
            logger.warning(f"memory.toml Schema 验证失败，回退 raw dict 加载: {e}")
            with open(memory_path, "r", encoding="utf-8-sig") as f:
                result["memory"] = tomlkit.load(f).unwrap()

    # ====== 6. storage.toml ======
    storage_path = config_dir / "storage.toml"
    if storage_path.exists():
        try:
            with open(storage_path, "r", encoding="utf-8-sig") as f:
                raw_storage = tomlkit.load(f).unwrap()
            loaded_data["storage.toml"] = raw_storage
            _apply_file_upgrade_hooks(config_dir, "storage.toml", StorageRootConfig, raw_storage, current_ver)
            storage_data, storage_report = _load_and_validate_schema(storage_path, StorageRootConfig)
            _filter_optional_container_missing(storage_report, StorageRootConfig)
            if storage_report.has_drift:
                batch_id = batch_id or datetime.now().strftime("%Y%m%d_%H%M%S")
                backup = _write_back_schema_file(
                    config_dir, "storage.toml", StorageRootConfig, storage_data, batch_id=batch_id
                )
                _log_drift_writeback(
                    "storage.toml", backup, config_dir, storage_report.missing, storage_report.redundant, current_ver
                )
                storage_data, storage_report = _load_and_validate_schema(storage_path, StorageRootConfig)
                _filter_optional_container_missing(storage_report, StorageRootConfig)
            result["storage"] = storage_data
            combined.redundant.extend(f"storage.{r}" for r in storage_report.redundant)
            combined.missing.extend(f"storage.{m}" for m in storage_report.missing)
        except Exception as e:
            logger.warning(f"storage.toml Schema 验证失败，回退 raw dict 加载: {e}")
            with open(storage_path, "r", encoding="utf-8-sig") as f:
                result["storage"] = tomlkit.load(f).unwrap()

    # ====== 7. background.toml ======
    background_path = config_dir / "background.toml"
    if background_path.exists():
        try:
            with open(background_path, "r", encoding="utf-8-sig") as f:
                raw_background = tomlkit.load(f).unwrap()
            loaded_data["background.toml"] = raw_background
            _apply_file_upgrade_hooks(config_dir, "background.toml", BackgroundRootConfig, raw_background, current_ver)
            background_data, background_report = _load_and_validate_schema(background_path, BackgroundRootConfig)
            _filter_optional_container_missing(background_report, BackgroundRootConfig)
            if background_report.has_drift:
                batch_id = batch_id or datetime.now().strftime("%Y%m%d_%H%M%S")
                backup = _write_back_schema_file(
                    config_dir, "background.toml", BackgroundRootConfig, background_data, batch_id=batch_id
                )
                _log_drift_writeback(
                    "background.toml",
                    backup,
                    config_dir,
                    background_report.missing,
                    background_report.redundant,
                    current_ver,
                )
                background_data, background_report = _load_and_validate_schema(background_path, BackgroundRootConfig)
                _filter_optional_container_missing(background_report, BackgroundRootConfig)
            result["background"] = background_data
            combined.redundant.extend(f"background.{r}" for r in background_report.redundant)
            combined.missing.extend(f"background.{m}" for m in background_report.missing)
        except Exception as e:
            logger.warning(f"background.toml Schema 验证失败，回退 raw dict 加载: {e}")
            with open(background_path, "r", encoding="utf-8-sig") as f:
                result["background"] = tomlkit.load(f).unwrap()

    return result, combined


def needs_generation(config_dir: Path) -> bool:
    """检查是否需要生成默认配置"""
    if not config_dir.exists():
        return True
    toml_files = list(config_dir.glob("*.toml"))
    return len(toml_files) == 0


def get_config_version(config_dir: Path) -> str | None:
    """从 core.toml 读取配置版本号"""
    core_path = config_dir / "core.toml"
    if not core_path.exists():
        return None
    with open(core_path, "r", encoding="utf-8-sig") as f:
        doc = tomlkit.load(f)
    meta = doc.get("meta", {})
    version = meta.get("version")
    return str(version) if version else None
