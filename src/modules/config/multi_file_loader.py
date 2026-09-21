"""多文件配置加载器（阶段化管线）

从 config/ 目录加载 7 个 TOML 配置文件，按固定阶段序执行：

    ① read_all_raw      全部文件读为 dict（缺失文件先行按 Schema 生成）
    ②③ 版本推进 + 升级钩子  每文件链跑区间钩子；跨文件钩子双写 + 双版本同升
    ④ Pydantic 校验     硬错——类型违约直接抛出（含字段 dotted path）
    ⑤ 漂移写回          全量写出 + 备份；采集器子段按注册表校验与补全
    ⑥ 合并视图          剥离 per-file ``[meta]`` 后按 scope 合并

配置文件结构（按域划分）:
    config/agents.toml      - 业务 Agent（含主播人设/上下文/后台维护段）
    config/collectors.toml  - 采集器（enabled 名单 + 各采集器段）
    config/tools.toml       - 工具提供者启用/配置
    config/avatar.toml      - 皮套（平台启用名单 + 平台成员段 + 口型共享件）
    config/model.toml       - LLM/VLM 模型配置（三层：providers/models/profiles）
    config/storage.toml     - 存储（顶层 [sqlite] + [memory]）
    config/infra.toml       - 基础设施（tts/subtitle/events/interceptors/dashboard/logging/simulator）

设计约定：
- **校验失败必须硬错**——不存在 raw dict 降级路径；加载失败由上层
  （ConfigService）翻译为启动失败。
- **meta 隔离**——每文件的 ``[meta]`` 段是文件私有元数据，合并视图装入前
  剥离；版本按文件独立读取（``get_config_version``）。
- **动态键子段的权威在组件包**——采集器/Agent 子段经组件注册表
  （``COMPONENT_SCHEMAS``）、工具提供者 ``config`` 子段经工具提供者注册表
  （``TOOL_PROVIDER_SCHEMAS``）分发校验与默认值补全（见
  ``_validate_collectors_sections`` / ``_validate_tool_provider_sections``）；
  静态命名段直接 typed 引用包内 Schema（如 ``VisionProviderConfig.config``）。
  TTS 引擎/字幕后端子段为 free-form dict，其编辑链路由 WebUI 侧的
  provider Schema 承担，加载管线不做补全。
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.items import AoT
from pydantic import BaseModel

from src.modules.config.schemas.base import BaseConfig, DriftReport, _set_toml_value
from src.modules.config.agents_schemas import AgentsRootConfig
from src.modules.config.avatar_schemas import AvatarPlatformConfig, AvatarRootConfig, PLATFORM_NAMES
from src.modules.config.collectors_schemas import CollectorsRootConfig
from src.modules.config.tools_schemas import ToolsConfig, ToolsRootConfig
from src.modules.config.errors import ConfigValidationError
from src.modules.config.model_schemas import LLMProfilesConfig, ModelConfig, ModelRootConfig
from src.modules.config.storage_schemas import StorageRootConfig
from src.modules.config.infra_schemas import InfraRootConfig
from src.modules.config.self_write_guard import mark_self_write
from src.modules.logging import get_logger

logger = get_logger("MultiFileLoader")

# 配置文件清单（按域划分）：agents / collectors / tools / avatar / model / storage / infra。
# avatar.toml 排在 tools.toml 之后：avatar 迁出跨文件钩子（tools/infra → avatar）以
# avatar.toml 为目标文件，目标 dict 在阶段①已就位；宿主文件（tools/infra）
# 先于 avatar 自身钩子被遍历，"先搬家、后做数据变换"的次序由此保证。
_CONFIG_FILES = [
    "agents.toml",
    "collectors.toml",
    "tools.toml",
    "avatar.toml",
    "model.toml",
    "storage.toml",
    "infra.toml",
]

# 文件 → 合并视图 scope
_FILE_SCOPES: dict[str, str] = {
    "agents.toml": "agents",
    "collectors.toml": "collectors",
    "tools.toml": "tools",
    "avatar.toml": "avatar",
    "model.toml": "model",
    "storage.toml": "storage",
    "infra.toml": "infra",
}

# 文件 → 根 Schema（中央树）
_ROOT_SCHEMAS: dict[str, type[BaseConfig]] = {
    "agents.toml": AgentsRootConfig,
    "collectors.toml": CollectorsRootConfig,
    "tools.toml": ToolsRootConfig,
    "avatar.toml": AvatarRootConfig,
    "model.toml": ModelConfig,
    "storage.toml": StorageRootConfig,
    "infra.toml": InfraRootConfig,
}

# 文件 → 生成时的头部注释
_FILE_COMMENTS: dict[str, str] = {
    "agents.toml": "业务 Agent 配置 - Amaidesu",
    "collectors.toml": "采集器配置 - Amaidesu",
    "tools.toml": "工具配置 - Amaidesu",
    "avatar.toml": "皮套配置 - Amaidesu",
    "model.toml": "模型配置 - LLM/VLM 参数",
    "storage.toml": "存储配置 - Amaidesu",
    "infra.toml": "基础设施配置 - Amaidesu",
}

# 装配一致性断言：根 Schema 的自描述文件名必须与本表键一致，
# 防止两处事实源漂移（类属性是权威，本表是加载侧索引）
for _fname, _cls in _ROOT_SCHEMAS.items():
    if _cls.__file_name__ != _fname:
        raise RuntimeError(
            f"根 Schema 自描述文件名不一致: {_cls.__name__} 声明 {_cls.__file_name__!r}, 索引键 {_fname!r}"
        )


def resolve_root_schema(scope: str) -> type[BaseConfig] | None:
    """合并视图 scope（= 文件名去后缀）→ 根 Schema 类；未知 scope 返回 None。"""
    file_name = next((f for f, s in _FILE_SCOPES.items() if s == scope), None)
    return _ROOT_SCHEMAS.get(file_name) if file_name else None


def validate_config_updates(config_dir: Path, file_name: str, updates: dict[str, Any]) -> None:
    """校验键级变更并入后的文档（只校验不写盘）。

    供批量写入口做事务前置：全部文件校验通过后再逐文件落盘，
    保证"任一校验失败 → 磁盘零写入"。

    Raises:
        ConfigValidationError: 合并后的文档未通过 Schema 校验
    """
    raw = _read_toml_dict(config_dir / file_name)
    _apply_updates_to_raw(raw, updates)
    _validate_file(file_name, raw)


def update_config_values(config_dir: Path, file_name: str, updates: dict[str, Any]) -> None:
    """把键级变更并入指定配置文件并经统一管线写回（对外写入口）。

    流程：读原始文档 → 应用点分键变更 → Schema 校验（硬错，ConfigValidationError
    携带文件名与字段路径）→ 全量序列化写回（备份 + 自写压标 + 注释重生成形态）。

    Args:
        config_dir: config/ 目录路径
        file_name: 目标文件名（六文件之一）
        updates: ``{文件内点分路径: 新值}``，路径不含 scope 前缀；
            同路径多次给定时后者覆盖前者

    Raises:
        ConfigValidationError: 合并后的文档未通过 Schema 校验（磁盘零写入）
    """
    raw = _read_toml_dict(config_dir / file_name)
    _apply_updates_to_raw(raw, updates)
    instance, _report = _validate_file(file_name, raw)
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    _write_back_schema_file(config_dir, file_name, _ROOT_SCHEMAS[file_name], instance, batch_id=batch_id)


def _apply_updates_to_raw(raw: dict[str, Any], updates: dict[str, Any]) -> None:
    """把 ``{文件内点分路径: 新值}`` 逐条写入原始文档 dict（原地修改）。"""
    for dotted_key, value in updates.items():
        parts = dotted_key.split(".")
        current = raw
        for part in parts[:-1]:
            node = current.get(part)
            if not isinstance(node, dict):
                node = {}
                current[part] = node
            current = node
        current[parts[-1]] = value


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
    """把 BaseModel 实例的字段填入 tomlkit table（禁 None：所有字段一律落盘）。"""
    sub_config = item.model_dump()
    for sub_name, sub_info in type(item).model_fields.items():
        value = sub_config.get(sub_name)
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
        elif isinstance(value, dict):
            # dict 子段（如 [llm_profiles.<name>]）——值可能是 BaseConfig
            # 模型，交给统一转换器递归展开为表
            table = _dict_to_toml_table(value)
            if field_info.description and value:
                doc.add(tomlkit.comment(field_info.description))
            doc[field_name] = table
            doc.add(tomlkit.nl())
            continue
        else:
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
    """把 BaseModel 实例序列化为 tomlkit Table（值 + description 注释；禁 None）。

    dict 值（free-form 子段）不加容器级 description 注释——注释插在父表流
    会与子表表头错位，且破坏按表头定位键值的文本消费者；子段内字段的
    注释由字段级 description 在具体 Schema 序列化路径中提供。

    字段值为 ``None`` 时直接跳过：TOML 原生不支持 null，Optional 字段
    在 dump 时保留 None，须由序列化器兜底（与 ``_generate_root_toml`` 的
    ``if field_value is None: continue`` 同源惯例）。
    """
    table = tomlkit.table()
    # tools 动态分类段（studio）的 dict 值走注册表注释化渲染
    is_tool_sections_host = isinstance(instance, ToolsConfig)
    for sub_name, sub_info in type(instance).model_fields.items():
        value = getattr(instance, sub_name)
        if value is None:
            # None = 未设置 / Optional 默认；不落盘，与 generate_toml_string 兜底一致
            continue
        if isinstance(value, BaseModel):
            inner = _table_from_model(value)
        elif isinstance(value, list) and value and all(isinstance(v, BaseModel) for v in value):
            inner = tomlkit.aot()
            for item in value:
                inner.append(_table_from_model(item))
        elif isinstance(value, dict):
            # 函数内 import 规避循环依赖：注册表会拉起各 provider 包
            from src.modules.config.registry import TOOL_PROVIDER_DOMAINS

            if is_tool_sections_host and sub_name in TOOL_PROVIDER_DOMAINS:
                inner = _tool_provider_sections_table(value, domain=sub_name)
            else:
                inner = _dict_to_toml_table(value)
        else:
            inner = value
        # 嵌套表（BaseModel / AoT）由 tomlkit 渲染到父段之后，行内注释会
        # 悬空在后续标量字段之前——仅标量与 free-form dict 字段加注释
        is_nested_table = isinstance(value, BaseModel) or (
            isinstance(value, list) and value and all(isinstance(v, BaseModel) for v in value)
        )
        if sub_info.description and not is_nested_table:
            table.add(tomlkit.comment(sub_info.description))
        table[sub_name] = inner

    # avatar 平台组段的未注册残留段（extra="allow" 保留在 extras，不在
    # model_fields 里）：原样输出，不因未注册而静默丢弃用户数据
    if isinstance(instance, AvatarPlatformConfig):
        for extra_name, extra_value in (instance.__pydantic_extra__ or {}).items():
            table[extra_name] = _dict_to_toml_table(extra_value) if isinstance(extra_value, dict) else extra_value
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


def _tool_provider_sections_table(sections: dict[str, Any], domain: str) -> Any:
    """把 tools 动态分类段（avatar/studio）序列化为 tomlkit Table。

    provider 段本体（enabled 等）走通用模型序列化；``config`` 子表经注册表
    查到包内 ConfigSchema 后按模型重建——字段 description 成为行前注释，
    落盘文件里选项含义可读。注册表未命中（残留段）或重建失败时降级为
    裸 dict 渲染并记日志：注释是可读性增益，不构成硬错理由。
    """
    # 函数内 import 规避循环依赖（与校验分支同款）
    from src.modules.config.registry import TOOL_PROVIDER_SCHEMAS

    table = tomlkit.table()
    for key, provider_cfg in sections.items():
        if isinstance(provider_cfg, BaseModel):
            inner = _table_from_model(provider_cfg)
        elif isinstance(provider_cfg, dict):
            inner = _dict_to_toml_table(provider_cfg)
        else:
            inner = provider_cfg
        schema_cls = TOOL_PROVIDER_SCHEMAS.get((domain, key))
        config_dict = getattr(provider_cfg, "config", None) if isinstance(provider_cfg, BaseModel) else None
        if schema_cls is not None and isinstance(config_dict, dict):
            try:
                inner["config"] = _table_from_model(schema_cls.from_dict(config_dict))
            except Exception as e:
                logger.warning(f"tools.{domain}.{key}.config 注释化序列化失败，降级为裸键: {e}")
        table[key] = inner
    return table


def _serialize_instance_to_toml(schema_cls: type[BaseModel], instance: BaseModel) -> str:
    """把配置实例序列化为多文件格式 TOML（顶层字段即顶层表/键值）。

    禁 None：所有 Schema 字段一律落盘（空值以空容器/空串形态写出）。
    ``extra="allow"`` 的 Schema（如 CollectorsRootConfig）携带的动态键
    （``[collectors.<name>]`` 子段）同样输出——只遍历 model_fields 会把
    子段静默丢弃，属于数据丢失缺陷。
    """
    doc = tomlkit.document()
    for field_name, field_info in schema_cls.model_fields.items():
        value = getattr(instance, field_name)
        if isinstance(value, BaseModel):
            table = _table_from_model(value)
        elif isinstance(value, list) and value and all(isinstance(v, BaseModel) for v in value):
            table = tomlkit.aot()
            for item in value:
                table.append(_table_from_model(item))
        elif isinstance(value, dict):
            # dict 子段（free-form）不加容器级注释：文本稳定性优先
            table = _dict_to_toml_table(value)
        else:
            if field_info.description:
                doc.add(tomlkit.comment(field_info.description))
            doc[field_name] = value
            doc.add(tomlkit.nl())
            continue
        if field_info.description and not isinstance(value, dict):
            doc.add(tomlkit.comment(field_info.description))
        doc[field_name] = table
        doc.add(tomlkit.nl())

    extras = getattr(instance, "__pydantic_extra__", None) or {}
    for key, value in extras.items():
        if isinstance(value, dict):
            doc[key] = _dict_to_toml_table(value)
        else:
            doc[key] = value
        doc.add(tomlkit.nl())
    return tomlkit.dumps(doc)


def _write_back_schema_file(
    config_dir: Path,
    file_name: str,
    schema_cls: type[BaseConfig],
    instance: BaseConfig,
    *,
    batch_id: str | None = None,
) -> Path | None:
    """漂移写回：内容不变短路 → 备份旧文件 → 全量序列化写盘 + 压自写标记。

    Returns:
        备份路径；内容未变化（短路，未写盘）时为 None。
    """
    file_path = config_dir / file_name
    content = _serialize_instance_to_toml(schema_cls, instance)

    has_bom = False
    try:
        with open(file_path, "rb") as f:
            has_bom = f.read(3) == b"\xef\xbb\xbf"
    except OSError:
        pass
    new_bytes = content.encode("utf-8-sig" if has_bom else "utf-8")

    # 内容不变短路：不写盘、不备份，避免 mtime 噪音与重写放大
    try:
        if file_path.read_bytes() == new_bytes:
            return None
    except OSError:
        pass

    backup_path = _backup_file(file_path, config_dir, batch_id=batch_id)
    mark_self_write(file_path)
    file_path.write_bytes(new_bytes)
    return backup_path


def _ensure_required_files(config_dir: Path) -> list[str]:
    """补齐缺失的必需配置文件（6 个域文件）——首启/单缺语义。

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


def _read_toml_dict(file_path: Path) -> dict[str, Any]:
    """读取单个 TOML 文件为 dict（兼容 UTF-8 BOM）。"""
    with open(file_path, "r", encoding="utf-8-sig") as f:
        return tomlkit.load(f).unwrap()


def _validate_collectors_sections(
    root_instance: CollectorsRootConfig,
    report: DriftReport,
) -> None:
    """按组件注册表校验采集器子段与 enabled 名单（阶段④ 的 collectors 分支）。

    - 子段名 / enabled 名不在注册表 → 跳过 + warning（已退役或残留段容忍），
      不抛 ConfigValidationError。采集器实例化侧（``factory.instantiate_collector``）
      同样做未知名跳过；此处保证加载期也不抛，让配置残留段平滑过渡。
    - 在册子段 → 包内 ConfigSchema 校验 + 漂移检测；漂移路径以
      ``<采集器名>.<字段>`` 前缀并入宿主文件报告
    - 校验后的干净子段 dict 回填 root 实例的 extras，供阶段⑤ 全量写回
    """
    # 函数内 import 规避循环依赖：组件注册表会拉起各组件包，
    # 而部分组件包（如采集器）转而引用本模块的加载能力
    from src.modules.config.registry import COMPONENT_SCHEMAS

    known = sorted(COMPONENT_SCHEMAS)
    for enabled_name in root_instance.enabled:
        if enabled_name not in COMPONENT_SCHEMAS:
            logger.warning(
                f"collectors.enabled 含未注册采集器名 {enabled_name!r}（合法名单：{known}），已跳过加载。"
                f"该名通常是已退役采集器（如 screen）的残留项；可从 enabled 列表移除。"
            )
            continue

    extras = root_instance.__pydantic_extra__ or {}
    for name in sorted(extras):
        schema_cls = COMPONENT_SCHEMAS.get(name)
        if schema_cls is None:
            logger.warning(
                f"collectors.toml 残留未注册段 [collectors.{name}]（合法名单：{known}），已跳过加载。"
                f"该段通常是已退役采集器的残留项；CollectorsRootConfig.extra='allow' 保留段不删除，"
                f"供人工复核后再清理。"
            )
            continue
        sub_raw = extras[name]
        if not isinstance(sub_raw, dict):
            raise ConfigValidationError(
                "collectors.toml",
                f"collectors.{name}",
                f"期望 TOML 表（dict），实际 {type(sub_raw).__name__}",
            )
        try:
            sub_instance, sub_report = schema_cls.from_dict_with_drift_check(sub_raw)
        except Exception as exc:
            raise ConfigValidationError("collectors.toml", f"collectors.{name}", f"子段校验失败: {exc}") from exc
        report.missing.extend(f"{name}.{m}" for m in sub_report.missing)
        report.redundant.extend(f"{name}.{r}" for r in sub_report.redundant)
        root_instance.__pydantic_extra__[name] = sub_instance.model_dump()


def _validate_tool_provider_sections(
    root_instance: ToolsRootConfig,
    report: DriftReport,
) -> None:
    """按工具提供者注册表校验 ``[tools.<domain>.<key>].config`` 子段（阶段④ 的 tools 分支）。

    与采集器分支同构（``_validate_collectors_sections``）：

    - 在册提供者段 → 包内 ConfigSchema 校验 + 漂移检测（缺键补默认、
      未知键剥离），漂移路径以 ``tools.<domain>.<key>.config.<字段>``
      前缀并入宿主文件报告；补全后的干净 dict 回填 ``.config`` 字段，
      供阶段⑤ 全量写回与运行时装配消费
    - 未注册段（已退役或残留）→ warning 跳过，原样保留，不抛硬错
    - 类型违约 → ConfigValidationError（携带完整 dotted path），错误
      从运行期装配失败前移到加载期
    """
    # 函数内 import 规避循环依赖：注册表会拉起各 provider 包
    from src.modules.config.registry import TOOL_PROVIDER_DOMAINS, TOOL_PROVIDER_SCHEMAS

    known = sorted(f"{d}.{k}" for d, k in TOOL_PROVIDER_SCHEMAS)
    tools = root_instance.tools
    # avatar 域的成员段已迁出为独立文件 avatar.toml（ToolsConfig 无 avatar
    # 字段），该域在此循环自然空转
    for domain in TOOL_PROVIDER_DOMAINS:
        sections = getattr(tools, domain, None) or {}
        for key in sorted(sections):
            provider_cfg = sections[key]
            schema_cls = TOOL_PROVIDER_SCHEMAS.get((domain, key))
            if schema_cls is None:
                logger.warning(
                    f"tools.toml 残留未注册提供者段 [tools.{domain}.{key}]（合法名单：{known}），"
                    f"config 子段跳过校验与补全，原样保留供人工复核。"
                )
                continue
            # 段本体未知键（enabled/config 之外的拼写错误等）：extra="allow" 保留、
            # 序列化时丢弃——计入报告让这次清理在日志与写回中可见
            section_extras = getattr(provider_cfg, "__pydantic_extra__", None) or {}
            report.redundant.extend(f"tools.{domain}.{key}.{k}" for k in sorted(section_extras))
            try:
                sub_instance, sub_report = schema_cls.from_dict_with_drift_check(provider_cfg.config)
            except Exception as exc:
                raise ConfigValidationError(
                    "tools.toml",
                    f"tools.{domain}.{key}.config",
                    f"提供者 config 子段校验失败: {exc}",
                ) from exc
            report.merge(f"tools.{domain}.{key}.config", sub_report)
            # 剥 None 再回填：Optional 字段的 None 不落盘（TOML 无 null 字面量），
            # 序列化侧的裸 dict 渲染路径不做 None 兜底
            provider_cfg.config = {k: v for k, v in sub_instance.model_dump().items() if v is not None}


def _validate_avatar_platform_sections(root_instance: AvatarRootConfig, report: DriftReport) -> None:
    """校验 ``[avatar.platform]`` 启用名单与残留段（阶段④ 的 avatar 分支）。

    成员段的漂移检测（缺键补默认、未知键剥离）由根 Schema 的递归
    ``from_dict_with_drift_check`` 经 typed 引用自动完成，本分支只补两类：

    - ``enabled`` 名单出现合法清单外的平台名 → 硬错。平台名封闭三值且
      无退役史（不同于采集器的退役名容忍跳过），表外名字装配期必然
      查不到 provider，错误前移到加载期并给出合法名单。
    - ``extra="allow"`` 保留下来的未注册成员段（如迁移残留的拼写错误段）
      → warning 提示人工复核，数据原样保留（序列化侧同步保留输出，
      不因未注册而静默丢弃）。
    """
    platform = root_instance.platform
    unknown = sorted(n for n in platform.enabled if n not in PLATFORM_NAMES)
    if unknown:
        raise ConfigValidationError(
            "avatar.toml",
            "avatar.platform.enabled",
            f"未注册平台名 {unknown}（合法名单：{sorted(PLATFORM_NAMES)}）",
        )
    extras = platform.__pydantic_extra__ or {}
    for name in sorted(extras):
        logger.warning(
            f"avatar.toml 残留未注册平台段 [avatar.platform.{name}]（合法名单：{sorted(PLATFORM_NAMES)}），"
            f"跳过校验，原样保留供人工复核。"
        )
    if extras:
        report.redundant.extend(f"platform.{name}" for name in sorted(extras))


def _validate_llm_profiles_closed_set(raw_data: dict[str, Any]) -> None:
    """``[llm_profiles]`` 封闭集合校验（加载期，未知用途即硬错）。

    成员清单的权威源是 ``LLMProfilesConfig`` 的显式字段集合；"缺"由字段
    缺省种子保证（漂移写回自动补齐），无需显式必填清单。未知键在漂移
    剥离之前先于原始数据上检查，保证硬错并指出未知键。
    """
    raw_profiles = raw_data.get("llm_profiles")
    if raw_profiles is None:
        return
    if not isinstance(raw_profiles, dict):
        raise ConfigValidationError(
            "model.toml",
            "llm_profiles",
            f"期望 TOML 表（dict），实际 {type(raw_profiles).__name__}",
        )
    known = set(LLMProfilesConfig.model_fields)
    unknown = sorted(set(raw_profiles) - known)
    if unknown:
        raise ConfigValidationError(
            "model.toml",
            "llm_profiles",
            f"未知用途 profile：{unknown}（封闭集合：{sorted(known)}）",
        )


def _validate_file(file_name: str, raw_data: dict[str, Any]) -> tuple[BaseConfig, DriftReport]:
    """阶段④：单文件 Pydantic 校验（硬错）。

    Returns:
        (校验后的实例（含补默认/剥冗余）, 漂移报告)

    Raises:
        ConfigValidationError: 类型违约 / 必填缺失 / 未注册名等——统一硬错载体，
            携带文件名与字段 dotted path，无降级路径。
    """
    schema_cls = _ROOT_SCHEMAS[file_name]
    try:
        instance, report = schema_cls.from_dict_with_drift_check(raw_data)
    except ConfigValidationError:
        raise
    except Exception as exc:
        raise ConfigValidationError(file_name, "", f"Schema 校验失败: {exc}") from exc
    _filter_optional_container_missing(report, schema_cls)
    if isinstance(instance, CollectorsRootConfig):
        _validate_collectors_sections(instance, report)
    if isinstance(instance, ToolsRootConfig):
        _validate_tool_provider_sections(instance, report)
    if isinstance(instance, AvatarRootConfig):
        _validate_avatar_platform_sections(instance, report)
    if isinstance(instance, ModelRootConfig):
        _validate_llm_profiles_closed_set(raw_data)
    return instance, report


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
    """阶段化加载 config/ 目录下全部 6 个 TOML 配置文件。

    阶段序：① 全读 → ② 跨文件钩子 → ③ 每文件钩子 → ④ 校验（硬错）→
    ⑤ 漂移写回 → ⑥ 合并视图（剥 ``[meta]``）。阶段边界均有日志标记，
    便于 QA 断言管线行为。

    Args:
        config_dir: config/ 目录路径

    Returns:
        (按 scope 合并的配置字典, 综合漂移报告)

    Raises:
        Exception: 任一文件校验失败（类型违约 / 未注册采集器段）——
            硬错语义，无 raw dict 降级路径。
    """
    # 组合根装配断言：组件 ConfigSchema 注册表必须完整
    from src.modules.config.registry import ensure_component_registry

    ensure_component_registry()

    generated = _ensure_required_files(config_dir)
    if generated:
        logger.info(f"[加载管线] 首启生成 {len(generated)} 个缺失文件: {generated}")

    # --- 阶段① read_all_raw ---
    raw_docs: dict[str, dict[str, Any]] = {fname: _read_toml_dict(config_dir / fname) for fname in _CONFIG_FILES}
    logger.info(f"[加载管线] 阶段① read_all_raw 完成（{len(_CONFIG_FILES)} 文件）")

    # --- 阶段②③ 版本推进 + 升级钩子（每文件链；跨文件钩子双写 + 双版本同升）---
    # 函数内 import：upgrade 模块的注册表被测试 patch，顶部 import 会使
    # patch 指向失效的绑定
    from src.modules.config.upgrade import advance_file_versions

    version_changes = advance_file_versions(raw_docs)
    logger.info(
        f"[加载管线] 阶段②③ 版本推进完成（推进文件 {len(version_changes)} 个）"
        if version_changes
        else "[加载管线] 阶段②③ 版本推进完成（全部文件已在基线）"
    )

    # --- 阶段④ 校验（硬错） + 阶段⑤ 漂移写回 ---
    combined = DriftReport()
    validated: dict[str, tuple[BaseConfig, DriftReport]] = {}
    for fname in _CONFIG_FILES:
        instance, report = _validate_file(fname, raw_docs[fname])
        validated[fname] = (instance, report)
    logger.info(f"[加载管线] 阶段④ Pydantic 校验完成（{len(_CONFIG_FILES)} 文件，硬错语义）")

    batch_id: str | None = None
    residuals: dict[str, DriftReport] = {}
    for fname, (instance, report) in validated.items():
        advanced = fname in version_changes
        if report.has_drift or advanced:
            batch_id = batch_id or datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = _write_back_schema_file(config_dir, fname, _ROOT_SCHEMAS[fname], instance, batch_id=batch_id)
            if report.has_drift:
                _log_drift_writeback(fname, backup, config_dir, report.missing, report.redundant)
            # 写回后磁盘内容 = 校验实例的序列化，残余漂移为净；
            # 返回报告语义 = "写回后的残余漂移"（漂移过程可见性走日志）
            residuals[fname] = DriftReport()
        else:
            residuals[fname] = report
    logger.info("[加载管线] 阶段⑤ 漂移写回完成")

    # --- 阶段⑥ 合并视图（剥 [meta]）---
    result: dict[str, Any] = {}
    for fname, (instance, _report) in validated.items():
        scope = _FILE_SCOPES[fname]
        data = instance.model_dump()
        data.pop("meta", None)
        result[scope] = data
        residual = residuals[fname]
        combined.redundant.extend(f"{scope}.{r}" for r in residual.redundant)
        combined.missing.extend(f"{scope}.{m}" for m in residual.missing)
    logger.info("[加载管线] 阶段⑥ 合并视图完成（meta 已剥离）")

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
