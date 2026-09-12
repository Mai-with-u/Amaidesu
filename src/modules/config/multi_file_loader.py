"""多文件配置加载器

从 config/ 目录加载 6 个 TOML 配置文件，使用 Pydantic Schema 验证并检测漂移。
首次运行时从 Schema 默认值生成带注释的配置文件。

配置文件结构（按域划分）:
    config/agents.toml      - 业务 Agent（含主播人设/上下文/后台维护段）
    config/collectors.toml  - 采集器（enabled 名单 + 各采集器段）
    config/tools.toml       - 工具提供者启用/配置
    config/model.toml       - LLM/VLM 模型配置
    config/storage.toml     - 存储（SQLite + 记忆子系统装配）
    config/infra.toml       - 基础设施（tts/subtitle/events/interceptors/dashboard/logging/simulator）

> **写回闭环**：全部文件均接入 ``_load_and_validate_schema``
> + ``_write_back_schema_file``，漂移字段会自动写回用户文件（缺失补默认、
> 冗余剥离）。未接入写回闭环的文件，其 Schema 变更不会落到用户文件。
> **meta 隔离**：每文件的 ``[meta]`` 段是文件私有元数据，合并视图在
> 装入前剥离——版本不进入全局命名空间，按文件独立取回。
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import tomlkit
from tomlkit.items import AoT

from src.modules.config.schemas.base import BaseConfig, DriftReport, _set_toml_value
from src.modules.config.agents_schemas import AgentsRootConfig
from src.modules.config.collectors_schemas import CollectorsRootConfig
from src.modules.config.tools_schemas import ToolsRootConfig
from src.modules.config.model_schemas import ModelConfig
from src.modules.config.storage_schemas import StorageRootConfig
from src.modules.config.infra_schemas import InfraRootConfig
from src.modules.logging import get_logger
from pydantic import BaseModel

logger = get_logger("MultiFileLoader")

# 配置文件清单（按域划分）：agents / collectors / tools / model / storage / infra
_CONFIG_FILES = [
    "agents.toml",
    "collectors.toml",
    "tools.toml",
    "model.toml",
    "storage.toml",
    "infra.toml",
]

# 文件 → 合并视图 scope
_FILE_SCOPES: dict[str, str] = {
    "agents.toml": "agents",
    "collectors.toml": "collectors",
    "tools.toml": "tools",
    "model.toml": "model",
    "storage.toml": "storage",
    "infra.toml": "infra",
}

# 文件 → 根 Schema（中央树）
_ROOT_SCHEMAS: dict[str, type[BaseConfig]] = {
    "agents.toml": AgentsRootConfig,
    "collectors.toml": CollectorsRootConfig,
    "tools.toml": ToolsRootConfig,
    "model.toml": ModelConfig,
    "storage.toml": StorageRootConfig,
    "infra.toml": InfraRootConfig,
}

# 文件 → 生成时的头部注释
_FILE_COMMENTS: dict[str, str] = {
    "agents.toml": "业务 Agent 配置 - Amaidesu",
    "collectors.toml": "采集器配置 - Amaidesu",
    "tools.toml": "工具配置 - Amaidesu",
    "model.toml": "模型配置 - LLM/VLM 参数",
    "storage.toml": "存储配置 - Amaidesu",
    "infra.toml": "基础设施配置 - Amaidesu",
}


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


def _populate_fields_table(table: Any, item: BaseModel) -> None:
    """把 BaseModel 实例的字段填入 tomlkit table（None 值跳过）。"""
    sub_config = item.model_dump()
    for sub_name, sub_info in type(item).model_fields.items():
        value = sub_config.get(sub_name)
        if value is None:
            continue
        if sub_info.description:
            table.add(tomlkit.comment(sub_info.description))
        _set_toml_value(table, sub_name, value)


def _generate_root_toml(file_name: str, schema_cls: type[BaseConfig]) -> str:
    """从根 Schema 默认值生成整文件 TOML（六文件共用的生成器）。

    顶层字段即顶层表/键值：BaseModel 字段展开为子表，list[BaseModel]
    字段展开为 array-of-tables（``[[name]]``）。
    """
    doc = tomlkit.document()
    doc.add(tomlkit.comment(_FILE_COMMENTS.get(file_name, f"{file_name} 配置 - Amaidesu")))
    doc.add(tomlkit.nl())

    instance = schema_cls()

    for field_name, field_info in schema_cls.model_fields.items():
        value = getattr(instance, field_name)

        if isinstance(value, BaseModel):
            table = tomlkit.table()
            _populate_fields_table(table, value)
        elif isinstance(value, list) and value and all(isinstance(v, BaseModel) for v in value):
            tables = []
            for item in value:
                item_table = tomlkit.table()
                _populate_fields_table(item_table, item)
                tables.append(item_table)
            if field_info.description:
                doc.add(tomlkit.comment(field_info.description))
            doc[field_name] = AoT(tables)
            doc.add(tomlkit.nl())
            continue
        else:
            if value is None:
                continue
            if field_info.description:
                doc.add(tomlkit.comment(field_info.description))
            doc[field_name] = value
            doc.add(tomlkit.nl())
            continue

        if field_info.description:
            doc.add(tomlkit.comment(field_info.description))
        doc[field_name] = table
        doc.add(tomlkit.nl())

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
    """把嵌套 dict 转 tomlkit Table（值为 Pydantic 模型时递归展开为表）。

    动态分类字段（如 ``avatar: Dict[str, AvatarProviderConfig]``）的模型值
    若不展开，tomlkit 无法序列化直接报错。
    """
    table = tomlkit.table()
    for key, value in data.items():
        if isinstance(value, BaseModel):
            table[key] = _table_from_model(value)
        elif isinstance(value, dict):
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


# 子段名 → ConfigSchema 延迟加载器：
# - TTS 引擎与字幕后端是基础设施，配置宿主为 infra.toml（[tts] / [subtitle]）
# - 形象/演播室 provider 的 ConfigSchema 由各自包内权威定义；装配期由
#   注册表驱动补全（参见 _complete_free_form_sections 的 providers 路径）。
# Schema 嵌在 Provider 类内部且 Provider 模块 import 较重（audio/网络依赖），
# 因此仅在补全流程实际运行时才加载；加载失败（依赖缺失）时跳过该子段。
_TTS_ENGINE_SCHEMA_LOADERS: dict[str, Callable[[], Optional[type[BaseModel]]]] = {
    "edge_tts": lambda: _try_import_provider_schema("src.modules.tts.edge_tts_tool", "EdgeTTSProvider"),
    "gptsovits": lambda: _try_import_provider_schema("src.modules.tts.gptsovits_tool", "GPTSoVITSProvider"),
    "omni_tts": lambda: _try_import_provider_schema("src.modules.tts.omni_tts_tool", "OmniTTSProvider"),
    "voicebox": lambda: _try_import_provider_schema("src.modules.tts.voicebox_tool", "VoiceboxProvider"),
}

_SUBTITLE_BACKEND_SCHEMA_LOADERS: dict[str, Callable[[], Optional[type[BaseModel]]]] = {
    "tk_gui": lambda: _try_import_provider_schema("src.modules.subtitle.backends.tk_gui_service", "SubtitleGuiService"),
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
        section_container: 宿主文件中承载各子段的容器 dict（如 infra.toml 的
            ``[tts]`` 段或 tools.toml 的 ``[tools.output.config]`` 段内存表示），
            补全结果同步回写。
        table_prefix: 子段表头前缀（``"tts"`` 或 ``"tools.output.config"``）。
        schema_loaders: 子段名 → ConfigSchema 延迟加载器。
        file_name: 宿主文件名（``"infra.toml"`` / ``"tools.toml"``）。
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
        # 探测默认实例：含必填字段的 schema 无法在空状态下构造，跳过补全
        try:
            defaults = schema_cls().model_dump()
        except Exception:
            continue
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


def _complete_free_form_sections(
    config_dir: Path,
    file_name: str,
    file_data: dict[str, Any],
    batch_id: str | None,
) -> list[str]:
    """按宿主文件分发 Provider 子段补全（free-form dict 不参与根 Schema 校验）。"""
    completed: list[str] = []
    if file_name == "infra.toml":
        tts_section = file_data.get("tts", {}) if isinstance(file_data, dict) else {}
        if isinstance(tts_section, dict):
            completed += _complete_provider_config_sections(
                config_dir,
                tts_section,
                table_prefix="tts",
                schema_loaders=_TTS_ENGINE_SCHEMA_LOADERS,
                file_name="infra.toml",
                batch_id=batch_id,
            )
        subtitle_section = file_data.get("subtitle", {}) if isinstance(file_data, dict) else {}
        if isinstance(subtitle_section, dict):
            completed += _complete_provider_config_sections(
                config_dir,
                subtitle_section,
                table_prefix="subtitle",
                schema_loaders=_SUBTITLE_BACKEND_SCHEMA_LOADERS,
                file_name="infra.toml",
                batch_id=batch_id,
            )
    elif file_name == "collectors.toml":
        collectors_section = file_data.get("collectors") if isinstance(file_data, dict) else None
        if isinstance(collectors_section, dict):
            from src.modules.config.registry import COMPONENT_SCHEMAS

            def _safe_collector_loader(name: str, schema_cls: type[BaseModel]):
                def _loader() -> Optional[type[BaseModel]]:
                    return schema_cls

                return _loader

            completed += _complete_provider_config_sections(
                config_dir,
                collectors_section,
                table_prefix="collectors",
                schema_loaders={
                    name: _safe_collector_loader(name, schema_cls) for name, schema_cls in COMPONENT_SCHEMAS.items()
                },
                file_name="collectors.toml",
                batch_id=batch_id,
            )
    return completed


def _write_back_schema_file(
    config_dir: Path,
    file_name: str,
    schema_cls: type[BaseConfig],
    user_data: dict[str, Any],
    *,
    batch_id: str | None = None,
) -> Path | None:
    """自动升级写回：备份旧文件 → 用户值合并（缺失补默认、冗余已剥离）→ 序列化写回。"""
    file_path = config_dir / file_name
    data = dict(user_data)

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
    """补齐缺失的必需配置文件（6 个域文件）。

    Returns:
        本次补齐的文件名列表
    """
    generated: list[str] = []
    for fname in _CONFIG_FILES:
        if (config_dir / fname).exists():
            continue
        logger.info(f"缺失配置文件 {fname}，自动生成...")
        (config_dir / fname).write_text(_generate_root_toml(fname, _ROOT_SCHEMAS[fname]), encoding="utf-8-sig")
        generated.append(fname)
    return generated


def generate_default_configs(config_dir: Path) -> None:
    """首次运行：从 Schema 生成默认配置文件（6 个域文件）"""
    config_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"生成默认配置到 {config_dir}")

    for fname in _CONFIG_FILES:
        path = config_dir / fname
        content = _generate_root_toml(fname, _ROOT_SCHEMAS[fname])
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


def _log_drift_writeback(
    file_label: str,
    backup_path: Path | None,
    config_dir: Path,
    missing: list[str],
    redundant: list[str],
) -> None:
    """统一的"已自动升级"日志格式（全部文件共用）。"""
    backup_rel = f", 备份: {backup_path.relative_to(config_dir)}" if backup_path else ""
    logger.info(
        f"{file_label} 已自动升级: "
        f"补齐 {len(missing)} 项({', '.join(missing) or '无'}), "
        f"清理 {len(redundant)} 项({', '.join(redundant) or '无'})"
        f"{backup_rel}"
    )


def load_config_dir(
    config_dir: Path,
) -> tuple[dict[str, Any], DriftReport]:
    """加载 config/ 目录下全部 6 个 TOML 配置文件（含漂移写回闭环）

    Args:
        config_dir: config/ 目录路径

    Returns:
        (按 scope 合并的配置字典, 综合漂移报告)

    加载闭环（覆盖全部文件，逐文件独立执行）：
    缺失文件自动补齐；Schema 验证 → 存在漂移（缺失/冗余字段）时备份 +
    写回（缺失补默认值、冗余删除）；free-form Provider 子段按其包内
    ConfigSchema 补全；``[meta]`` 段在装入合并视图前剥离（meta 隔离）。
    """
    # 函数内 import 规避循环依赖：组件注册表会拉起各组件包，
    # 而部分组件包（如采集器）转而引用本模块的加载能力
    from src.modules.config.registry import ensure_component_registry

    # 组合根装配断言：组件 ConfigSchema 注册表必须完整
    ensure_component_registry()

    _ensure_required_files(config_dir)

    combined = DriftReport()
    result: dict[str, Any] = {}
    batch_id: str | None = None

    for file_name in _CONFIG_FILES:
        scope = _FILE_SCOPES[file_name]
        schema_cls = _ROOT_SCHEMAS[file_name]
        file_path = config_dir / file_name
        if not file_path.exists():
            continue

        try:
            data, report = _load_and_validate_schema(file_path, schema_cls)
            _filter_optional_container_missing(report, schema_cls)
            if report.has_drift:
                batch_id = batch_id or datetime.now().strftime("%Y%m%d_%H%M%S")
                backup = _write_back_schema_file(config_dir, file_name, schema_cls, data, batch_id=batch_id)
                _log_drift_writeback(file_name, backup, config_dir, report.missing, report.redundant)
                data, report = _load_and_validate_schema(file_path, schema_cls)
                _filter_optional_container_missing(report, schema_cls)

            completed = _complete_free_form_sections(config_dir, file_name, data, batch_id)
            if completed:
                # 补全改变了文件，重新加载以取到与磁盘一致的最新内容
                data, report = _load_and_validate_schema(file_path, schema_cls)
                _filter_optional_container_missing(report, schema_cls)

            data.pop("meta", None)
            result[scope] = data
            combined.redundant.extend(f"{scope}.{r}" for r in report.redundant)
            combined.missing.extend(f"{scope}.{m}" for m in report.missing)
        except Exception as e:
            logger.warning(f"{file_name} Schema 验证失败，回退 raw dict 加载: {e}")
            with open(file_path, "r", encoding="utf-8-sig") as f:
                raw = tomlkit.load(f).unwrap()
            raw.pop("meta", None)
            result[scope] = raw

    return result, combined


def needs_generation(config_dir: Path) -> bool:
    """检查是否需要生成默认配置"""
    if not config_dir.exists():
        return True
    toml_files = list(config_dir.glob("*.toml"))
    return len(toml_files) == 0


def get_config_version(config_dir: Path, file_name: str = "agents.toml") -> str | None:
    """读取指定配置文件 ``[meta].version`` 的结构版本号"""
    file_path = config_dir / file_name
    if not file_path.exists():
        return None
    with open(file_path, "r", encoding="utf-8-sig") as f:
        doc = tomlkit.load(f)
    meta = doc.get("meta", {})
    version = meta.get("version")
    return str(version) if version else None
