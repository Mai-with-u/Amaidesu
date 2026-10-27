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
    config/avatar.toml      - 虚拟形象（平台启用名单 + 平台成员段 + 口型共享件）
    config/model.toml       - LLM/VLM 模型配置（三层：providers/models/profiles）
    config/storage.toml     - 存储（顶层 [sqlite]）
    config/infra.toml       - 基础设施（tts/subtitle/events/interceptors/dashboard/logging/simulator）

设计约定：
- **校验失败必须硬错**——不存在 raw dict 降级路径；加载失败由上层
  （ConfigService）翻译为启动失败。
- **meta 隔离**——每文件的 ``[meta]`` 段是文件私有元数据，合并视图装入前
  剥离；版本按文件独立读取（``get_config_version``）。
- **动态键子段的权威在组件包**——采集器/Agent 子段经组件注册表
  （``COMPONENT_SCHEMAS``）、工具提供者 ``config`` 子段经工具提供者注册表
  （``TOOL_PROVIDER_SCHEMAS``）分发校验与默认值补全；静态命名段直接 typed
  引用包内 Schema（如 ``VisionProviderConfig.config``）。各域动态内容的
  校验机制统一为"每域校验声明"参数化的单一分支（``_FILE_VALIDATION_SPECS``
  / ``_apply_file_validation``），新增动态键域只需加一条声明。
  TTS 引擎/字幕后端子段为 free-form dict，其编辑链路由 WebUI 侧的
  provider Schema 承担，加载管线不做补全。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import tomlkit
from pydantic import BaseModel

from src.modules.config.schemas.base import BaseConfig, DriftReport, _set_toml_value
from src.modules.config.layout import CONFIG_FILE_NAMES, CONFIG_FILE_TO_SCOPE
from src.modules.config.agents_schemas import AgentsRootConfig
from src.modules.config.avatar_schemas import AvatarPlatformConfig, AvatarRootConfig, PLATFORM_NAMES
from src.modules.config.collectors_schemas import CollectorsRootConfig
from src.modules.config.tools_schemas import ToolsConfig, ToolsRootConfig
from src.modules.config.errors import ConfigValidationError
from src.modules.config.model_schemas import LLMProfilesConfig, ModelConfig
from src.modules.config.storage_schemas import StorageRootConfig
from src.modules.config.infra_schemas import InfraRootConfig
from src.modules.config.self_write_guard import mark_self_write
from src.modules.logging import get_logger

logger = get_logger("MultiFileLoader")

# 配置文件清单（按域划分）与文件 → scope 映射的单一事实源在 layout 模块，
# 本模块只保留根 Schema 索引（类属性是权威，本表是加载侧索引）。

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
    "avatar.toml": "虚拟形象配置 - Amaidesu",
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
    file_name = next((f for f, s in CONFIG_FILE_TO_SCOPE.items() if s == scope), None)
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
        file_name: 目标文件名（七文件之一）
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


def _dump_without_none(instance: BaseModel) -> dict[str, Any]:
    """模型 → dict 并递归剥除 None（本文件唯一的 None 剥离点）。

    TOML 无 null 字面量，任意层级的 None 都会炸序列化；model_dump 会展开
    嵌套子模型，浅层剥不够，段内部的 Optional None 同样要剥。在册段回填
    dict 与默认值 dict 共用本出口。
    """
    return instance.model_dump(exclude_none=True)


def _default_section_dict(schema_cls: type[BaseConfig]) -> dict[str, Any]:
    """单段默认值 dict（物化器与生成路径共用）。"""
    return _dump_without_none(schema_cls())


def _materializable_sections(file_name: str) -> dict[str, dict[str, Any]] | None:
    """按域声明构造配置文件的物化段全集（无可物化域返回 None）。

    生成路径与加载管线共用同一物化语义：首次生成/单缺重建的文件直接是
    "可配置全集"的物化快照，用户打开即见全部段模板。哪些域的缺失段会被
    物化补默认由每域校验声明的 ``materialize_registry`` 表达（当前仅
    collectors），扩展物化范围即在该域声明上登记。
    """
    spec = _FILE_VALIDATION_SPECS.get(file_name)
    if spec is None or spec.materialize_registry is None:
        return None
    registry_of = spec.materialize_registry
    # 函数内 import 规避循环依赖（同 _collector_registry）：
    # 注册表由组件目录惰性填充，生成路径没有"加载开头 ensure"的既定
    # 时序兜底，必须先 ensure——否则首启生成时全集为空、静默生成骨架
    from src.modules.component_catalog import ensure_component_registry

    ensure_component_registry()
    return {name: _default_section_dict(schema_cls) for name, schema_cls in sorted(registry_of().items())}


def _render_root_toml(
    schema_cls: type[BaseModel],
    instance: BaseModel,
    *,
    header_comment: str | None,
    model_table: Callable[[BaseModel], Any],
    dict_comment: bool,
    tail_sections: dict[str, Any] | None,
) -> str:
    """根 Schema 实例 → 整文件 TOML 的统一渲染（首启生成与漂移写回共用）。

    顶层字段即顶层表/键值：BaseModel 字段展开为子表，list[BaseModel] 字段
    展开为 array-of-tables（``[[name]]``），dict 展开为 free-form 表，其余
    为顶层标量。两处调用只剩三个口径差异，都由参数表达：

    - ``header_comment``：生成路径带文件头注释，写回路径不带（写回的注释
      全部来自字段 description，头部原样保留用户所见布局）
    - ``model_table``：段级表格渲染器——生成路径带每字段 description 注释
      （``_populate_fields_table``），写回路径走注册表注释化渲染
      （``_table_from_model``，含 None 跳过与工具提供者段重建）
    - ``dict_comment``：free-form dict 字段是否加容器级 description 注释
      （生成路径对非空 dict 加；写回路径一律不加，文本稳定性优先）

    ``tail_sections`` 追加在静态字段之后（生成路径为物化段全集，按名排序
    后传入；写回路径为 ``extra="allow"`` 的动态键残留，按原序传入）。
    """
    doc = tomlkit.document()
    if header_comment is not None:
        doc.add(tomlkit.comment(header_comment))
        doc.add(tomlkit.nl())

    for field_name, field_info in schema_cls.model_fields.items():
        value = getattr(instance, field_name)

        if isinstance(value, BaseModel):
            table = model_table(value)
        elif isinstance(value, list) and value and all(isinstance(v, BaseModel) for v in value):
            table = tomlkit.aot()
            for item in value:
                table.append(model_table(item))
        elif isinstance(value, dict):
            # dict 子段（如 [llm_profiles.<name>]）——值可能是 BaseConfig
            # 模型，交给统一转换器递归展开为表
            table = _dict_to_toml_table(value)
        else:
            if field_info.description:
                doc.add(tomlkit.comment(field_info.description))
            doc[field_name] = value
            doc.add(tomlkit.nl())
            continue

        if field_info.description and (not isinstance(value, dict) or (dict_comment and value)):
            doc.add(tomlkit.comment(field_info.description))
        doc[field_name] = table
        doc.add(tomlkit.nl())

    if tail_sections:
        for name, value in tail_sections.items():
            doc[name] = _dict_to_toml_table(value) if isinstance(value, dict) else value
            doc.add(tomlkit.nl())

    return tomlkit.dumps(doc)


def _generate_root_toml(
    file_name: str,
    schema_cls: type[BaseConfig],
    extra_sections: dict[str, dict[str, Any]] | None = None,
) -> str:
    """从根 Schema 默认值生成整文件 TOML（七文件共用的生成器）。

    动态键段不在静态字段里，由 ``extra_sections``（物化段全集）追加为顶层表。
    """

    def populate_table(item: BaseModel) -> Any:
        table = tomlkit.table()
        _populate_fields_table(table, item)
        return table

    return _render_root_toml(
        schema_cls,
        schema_cls(),
        header_comment=_FILE_COMMENTS.get(file_name, f"{file_name} 配置 - Amaidesu"),
        model_table=populate_table,
        dict_comment=True,
        tail_sections=dict(sorted(extra_sections.items())) if extra_sections else None,
    )


def _table_from_model(instance: BaseModel) -> Any:
    """把 BaseModel 实例序列化为 tomlkit Table（值 + description 注释；禁 None）。

    dict 值（free-form 子段）不加容器级 description 注释——注释插在父表流
    会与子表表头错位，且破坏按表头定位键值的文本消费者；子段内字段的
    注释由字段级 description 在具体 Schema 序列化路径中提供。

    字段值为 ``None`` 时直接跳过：TOML 原生不支持 null，Optional 字段
    在 dump 时保留 None，须由序列化器兜底（统一渲染器 ``_render_root_toml``
    的段级口径由此处承担）。
    """
    table = tomlkit.table()
    # tools 动态分类段（studio）的 dict 值走注册表注释化渲染
    is_tool_sections_host = isinstance(instance, ToolsConfig)
    for sub_name, sub_info in type(instance).model_fields.items():
        value = getattr(instance, sub_name)
        if value is None:
            # None = 未设置 / Optional 默认；TOML 不支持 null，直接跳过不落盘
            continue
        if isinstance(value, BaseModel):
            inner = _table_from_model(value)
        elif isinstance(value, list) and value and all(isinstance(v, BaseModel) for v in value):
            inner = tomlkit.aot()
            for item in value:
                inner.append(_table_from_model(item))
        elif isinstance(value, dict):
            # 函数内 import 规避循环依赖：域清单是组件目录的静态派生量（不拉起 provider 包）
            from src.modules.component_catalog import TOOL_PROVIDER_DOMAINS

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

    禁 None：所有 Schema 字段一律落盘（空值以空容器/空串形态写出；段级
    渲染器 ``_table_from_model`` 对残余 None 直接跳过——TOML 无 null 字面量）。
    ``extra="allow"`` 的 Schema（如 CollectorsRootConfig）携带的动态键
    （``[collectors.<name>]`` 子段）同样输出——只遍历 model_fields 会把
    子段静默丢弃，属于数据丢失缺陷。
    """
    extras = getattr(instance, "__pydantic_extra__", None) or {}
    return _render_root_toml(
        schema_cls,
        instance,
        header_comment=None,
        model_table=_table_from_model,
        dict_comment=False,
        tail_sections=extras or None,
    )


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

    # 统一无 BOM 落盘：存量带 BOM 的文件借此在下次写回时自然剥离
    new_bytes = content.encode("utf-8")

    # 内容不变短路：不写盘、不备份，避免 mtime 噪音与重写放大
    try:
        if file_path.read_bytes() == new_bytes:
            return None
    except OSError:
        # 读取失败即无法确认内容一致，放弃短路、继续走完整写盘路径
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
    for fname in CONFIG_FILE_NAMES:
        if (config_dir / fname).exists():
            continue
        logger.info(f"缺失配置文件 {fname}，自动生成...")
        content = _generate_root_toml(fname, _ROOT_SCHEMAS[fname], extra_sections=_materializable_sections(fname))
        (config_dir / fname).write_text(content, encoding="utf-8")
        generated.append(fname)
    return generated


def generate_default_configs(config_dir: Path) -> None:
    """首次运行：从 Schema 生成默认配置文件（6 个域文件）"""
    config_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"生成默认配置到 {config_dir}")

    for fname in CONFIG_FILE_NAMES:
        path = config_dir / fname
        content = _generate_root_toml(fname, _ROOT_SCHEMAS[fname], extra_sections=_materializable_sections(fname))
        path.write_text(content, encoding="utf-8")
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


# ---------------------------------------------------------------------------
# 阶段④ 每域校验声明与统一分支
#
# 各域动态内容的校验机制同构：enabled 名单封闭集 → 未知段封闭集 → 物化
# 补缺 → 在册段按注册表 Schema 校验 + 干净 dict 回填。机制只实现一遍
# （``_apply_file_validation``），域间既有差异（名单来源、合法清单、错误
# 文案、段枚举与回填形态、是否物化补缺）全部落在本表声明——新增一个
# 动态键域只需常规 Schema 注册 + 这里加一条声明。
# 名单与注册表经命名取值函数延迟求值：注册表由组件目录惰性填充，模块
# 导入期尚无内容；函数内 import 同时规避 config 包对组件实现包的依赖
# （先例：组件目录的延迟加载器模式）。
# ---------------------------------------------------------------------------


def _expect_table_error(value: Any) -> str:
    """ "段期望是 TOML 表"的统一错误文案。"""
    return f"期望 TOML 表（dict），实际 {type(value).__name__}"


@dataclass(frozen=True)
class _ClosedSetCheck:
    """封闭名单校验声明：名单来源 + 合法清单 + 硬错定位与文案。

    ``detail`` 是硬错文案的单一源：占位符 ``{unknown}``（未知名列表）、
    ``{known}``（合法名单）、``{identity}``（首个未知名）由统一校验分支
    参数化拼接；各域措辞差异（名词、出路提示、有无提示）由声明值表达。
    """

    names: Callable[[BaseConfig, dict[str, Any]], list[str]]  # 文件中出现的名字
    known: Callable[[], list[str]]  # 合法清单（延迟求值，保持排序）
    error_path: Callable[[str], str]  # 硬错定位 dotted path（入参为首个未知名）
    detail: str  # 硬错文案模板


@dataclass(frozen=True)
class _SectionEntry:
    """单个动态段在当前实例上的校验视图（输入、错误定位与回填点）。"""

    identity: str  # 段身份（注册表键的字符串形态）
    raw: Any  # 待校验的段数据
    unknown_error_path: str  # 未注册 / 非表硬错定位
    validate_error_path: str  # 段 Schema 校验失败硬错定位
    drift_prefix: str  # 子段漂移路径并入宿主报告的前缀
    backfill: Callable[[dict[str, Any]], None]  # 干净 dict 回填点
    extra_redundant_keys: tuple[str, ...] = ()  # 段本体未注册残留键（计入冗余报告）


@dataclass(frozen=True)
class _SectionCheck:
    """注册表驱动动态键域的逐段校验声明。"""

    schema_of: Callable[[str], type[BaseConfig] | None]  # 段身份 → ConfigSchema（未注册为 None）
    known_identities: Callable[[], list[str]]  # 合法段身份清单（延迟求值，保持排序）
    sections: Callable[[BaseConfig], list[_SectionEntry]]  # 当前文件里的段视图枚举
    unknown_detail: str  # 未注册段硬错文案模板（{identity} / {known}）
    validate_fail_label: str  # 段 Schema 校验失败的文案前缀（统一拼接 ": {异常}"）


@dataclass(frozen=True)
class _FileValidationSpec:
    """单个配置文件的动态内容校验声明（无声明的域不做动态内容校验）。"""

    file_name: str
    enabled: _ClosedSetCheck | None = None
    unknown_sections: _ClosedSetCheck | None = None
    sections: _SectionCheck | None = None
    # 缺段物化：返回"注册名 → ConfigSchema"（延迟求值）；None = 本域缺失段不物化补默认
    materialize_registry: Callable[[], dict[str, type[BaseConfig]]] | None = None


# -- collectors.toml 域声明 ---------------------------------------------------


def _collector_enabled_names(instance: BaseConfig, _raw: dict[str, Any]) -> list[str]:
    return list(instance.enabled)


def _collector_known_names() -> list[str]:
    # 函数内 import 规避循环依赖：注册表由组件目录惰性填充
    from src.modules.config.registry import COLLECTOR_SCHEMAS

    return sorted(COLLECTOR_SCHEMAS)


def _collector_section_identities(instance: BaseConfig, _raw: dict[str, Any]) -> list[str]:
    """文件里的动态段名（根 Schema ``extra="allow"`` 保留下来的 extras 键）。"""
    extras = getattr(instance, "__pydantic_extra__", None) or {}
    return sorted(extras)


def _collector_registry() -> dict[str, type[BaseConfig]]:
    # 函数内 import 规避循环依赖：注册表由组件目录惰性填充
    from src.modules.config.registry import COLLECTOR_SCHEMAS

    return COLLECTOR_SCHEMAS


def _collector_schema_of(identity: str) -> type[BaseConfig] | None:
    from src.modules.config.registry import COLLECTOR_SCHEMAS

    return COLLECTOR_SCHEMAS.get(identity)


def _extras_backfill(extras: dict[str, Any], name: str) -> Callable[[dict[str, Any]], None]:
    """原位回填闭包：干净 dict 写回实例 extras 的同名键。"""

    def _apply(clean: dict[str, Any]) -> None:
        extras[name] = clean

    return _apply


def _collector_section_entries(instance: BaseConfig) -> list[_SectionEntry]:
    """collectors.toml 的段视图：extras 里的段 dict，校验后原位回填。"""
    extras = getattr(instance, "__pydantic_extra__", None) or {}
    return [
        _SectionEntry(
            identity=name,
            raw=extras[name],
            unknown_error_path=f"collectors.{name}",
            validate_error_path=f"collectors.{name}",
            drift_prefix=name,
            backfill=_extras_backfill(extras, name),
        )
        for name in sorted(extras)
    ]


# -- tools.toml 域声明 --------------------------------------------------------


def _tool_provider_known_identities() -> list[str]:
    # 函数内 import 规避循环依赖：注册表由组件目录惰性填充
    from src.modules.config.registry import TOOL_PROVIDER_SCHEMAS

    return sorted(f"{d}.{k}" for d, k in TOOL_PROVIDER_SCHEMAS)


def _tool_provider_schema_of(identity: str) -> type[BaseConfig] | None:
    # 函数内 import 规避循环依赖：注册表由组件目录惰性填充
    from src.modules.config.registry import TOOL_PROVIDER_SCHEMAS

    domain, _, key = identity.partition(".")
    return TOOL_PROVIDER_SCHEMAS.get((domain, key))


def _provider_config_backfill(provider_cfg: Any) -> Callable[[dict[str, Any]], None]:
    """回填闭包：干净 dict 写回提供者段本体的 ``config`` 字段。"""

    def _apply(clean: dict[str, Any]) -> None:
        provider_cfg.config = clean

    return _apply


def _tool_section_entries(instance: BaseConfig) -> list[_SectionEntry]:
    """tools.toml 的段视图：各分类段下的提供者段（``[tools.<domain>.<key>]``）。

    avatar 域的成员段已迁出为独立文件 avatar.toml（ToolsConfig 无 avatar
    字段），该域在此枚举自然空转。段本体未注册残留键（enabled/config 之外
    的拼写错误等）计入冗余报告。
    """
    # 函数内 import 规避循环依赖：域清单是组件目录的静态派生量（不拉起 provider 包）
    from src.modules.component_catalog import TOOL_PROVIDER_DOMAINS

    tools = instance.tools
    entries: list[_SectionEntry] = []
    for domain in TOOL_PROVIDER_DOMAINS:
        sections = getattr(tools, domain, None) or {}
        for key in sorted(sections):
            provider_cfg = sections[key]
            entries.append(
                _SectionEntry(
                    identity=f"{domain}.{key}",
                    raw=provider_cfg.config,
                    unknown_error_path=f"tools.{domain}.{key}",
                    validate_error_path=f"tools.{domain}.{key}.config",
                    drift_prefix=f"tools.{domain}.{key}.config",
                    extra_redundant_keys=tuple(sorted(getattr(provider_cfg, "__pydantic_extra__", None) or {})),
                    backfill=_provider_config_backfill(provider_cfg),
                )
            )
    return entries


# -- avatar.toml 域声明 -------------------------------------------------------


def _avatar_enabled_names(instance: BaseConfig, _raw: dict[str, Any]) -> list[str]:
    return list(instance.platform.enabled)


def _avatar_platform_known_names() -> list[str]:
    return sorted(PLATFORM_NAMES)


def _avatar_section_identities(instance: BaseConfig, _raw: dict[str, Any]) -> list[str]:
    """文件里的未注册成员段（``extra="allow"`` 保留下来的 platform extras 键）。"""
    extras = instance.platform.__pydantic_extra__ or {}
    return sorted(extras)


# -- model.toml 域声明 --------------------------------------------------------


def _llm_profile_identities(_instance: BaseConfig, raw_data: dict[str, Any]) -> list[str]:
    """原始数据里的 ``[llm_profiles]`` 键（缺段为空；非表即硬错）。

    在漂移剥离之前先于原始数据上检查，保证未知键硬错且点名。
    """
    raw_profiles = raw_data.get("llm_profiles")
    if raw_profiles is None:
        return []
    if not isinstance(raw_profiles, dict):
        raise ConfigValidationError("model.toml", "llm_profiles", _expect_table_error(raw_profiles))
    return sorted(raw_profiles)


def _llm_profile_known_names() -> list[str]:
    """``[llm_profiles]`` 封闭集合的权威源：LLMProfilesConfig 的显式字段集合。

    "缺"由字段缺省种子保证（漂移写回自动补齐），无需显式必填清单。
    """
    return sorted(LLMProfilesConfig.model_fields)


# -- 声明登记表（新增动态键域 = 常规 Schema 注册 + 这里加一条声明）---------------

_FILE_VALIDATION_SPECS: dict[str, _FileValidationSpec] = {
    "collectors.toml": _FileValidationSpec(
        file_name="collectors.toml",
        enabled=_ClosedSetCheck(
            names=_collector_enabled_names,
            known=_collector_known_names,
            error_path=lambda _first: "collectors.enabled",
            detail=(
                "未注册采集器名 {unknown}（合法名单：{known}）。"
                "若因程序升级导致，请为该采集器的退役/改名补迁移钩子；若为手写残留，请从 enabled 移除"
            ),
        ),
        unknown_sections=_ClosedSetCheck(
            names=_collector_section_identities,
            known=_collector_known_names,
            error_path=lambda first: f"collectors.{first}",
            detail=(
                "未注册采集器段 {unknown}（合法名单：{known}）。"
                "若因程序升级导致，请补迁移钩子搬移旧结构；若为手写残留，请删除该段"
            ),
        ),
        sections=_SectionCheck(
            schema_of=_collector_schema_of,
            known_identities=_collector_known_names,
            sections=_collector_section_entries,
            unknown_detail=(
                "未注册采集器段 [{identity}]（合法名单：{known}）。"
                "若因程序升级导致，请补迁移钩子搬移旧结构；若为手写残留，请删除该段"
            ),
            validate_fail_label="子段校验失败",
        ),
        materialize_registry=_collector_registry,
    ),
    "tools.toml": _FileValidationSpec(
        file_name="tools.toml",
        sections=_SectionCheck(
            schema_of=_tool_provider_schema_of,
            known_identities=_tool_provider_known_identities,
            sections=_tool_section_entries,
            unknown_detail=(
                "未注册提供者段 [tools.{identity}]（合法名单：{known}）。"
                "若因程序升级导致，请补迁移钩子搬移旧结构；若为手写残留，请删除该段"
            ),
            validate_fail_label="提供者 config 子段校验失败",
        ),
    ),
    "avatar.toml": _FileValidationSpec(
        file_name="avatar.toml",
        enabled=_ClosedSetCheck(
            names=_avatar_enabled_names,
            known=_avatar_platform_known_names,
            error_path=lambda _first: "avatar.platform.enabled",
            detail="未注册平台名 {unknown}（合法名单：{known}）",
        ),
        unknown_sections=_ClosedSetCheck(
            names=_avatar_section_identities,
            known=_avatar_platform_known_names,
            error_path=lambda first: f"avatar.platform.{first}",
            detail="未注册平台段 {unknown}（合法名单：{known}）。若为手写残留，请删除该段",
        ),
    ),
    "model.toml": _FileValidationSpec(
        file_name="model.toml",
        unknown_sections=_ClosedSetCheck(
            names=_llm_profile_identities,
            known=_llm_profile_known_names,
            error_path=lambda _first: "llm_profiles",
            detail="未知用途 profile：{unknown}（封闭集合：{known}）",
        ),
    ),
}


def _check_closed_set(
    file_name: str,
    check: _ClosedSetCheck,
    instance: BaseConfig,
    raw_data: dict[str, Any],
) -> None:
    """封闭名单校验：名单出现合法清单外的条目 → 硬错（点名 + 合法名单 + 出路提示）。"""
    known = check.known()
    unknown = sorted(name for name in check.names(instance, raw_data) if name not in set(known))
    if unknown:
        raise ConfigValidationError(
            file_name,
            check.error_path(unknown[0]),
            check.detail.format(unknown=unknown, known=known, identity=unknown[0]),
        )


def _materialize_missing_sections(
    spec: _FileValidationSpec,
    instance: BaseConfig,
    report: DriftReport,
) -> None:
    """按域注册表为文件缺的段补默认值（物化器，加载管线与生成路径共用）。

    只补在册缺段，不触碰未知段（未知段的处置由封闭集校验负责）；补出的段
    计入漂移报告随阶段⑤写回落盘——文件由此成为"可配置全集"的物化快照：
    在册段必在文件，未启用的组件也可先配后启。
    """
    registry = spec.materialize_registry()
    extras = getattr(instance, "__pydantic_extra__", None)
    if extras is None:
        extras = {}
        instance.__pydantic_extra__ = extras
    for name, schema_cls in sorted(registry.items()):
        if name in extras:
            continue
        extras[name] = _default_section_dict(schema_cls)
        report.missing.append(name)


def _validate_registry_sections(
    file_name: str,
    check: _SectionCheck,
    instance: BaseConfig,
    report: DriftReport,
) -> None:
    """在册段逐段校验 + 干净 dict 回填（机制统一分支）。

    - 未注册段 → 硬错（文案见各域声明的模板）
    - 段数据非表 → 硬错
    - 段本体未注册残留键 → 计入冗余报告（随全域冗余硬错聚合拦截）
    - 段 Schema 校验 → 漂移并入宿主报告；干净 dict（剥 None）经段声明
      的回填点写回，供阶段⑤全量写回与运行时装配消费
    """
    for view in check.sections(instance):
        schema_cls = check.schema_of(view.identity)
        if schema_cls is None:
            raise ConfigValidationError(
                file_name,
                view.unknown_error_path,
                check.unknown_detail.format(identity=view.identity, known=check.known_identities()),
            )
        if not isinstance(view.raw, dict):
            raise ConfigValidationError(file_name, view.unknown_error_path, _expect_table_error(view.raw))
        report.redundant.extend(f"{view.unknown_error_path}.{k}" for k in view.extra_redundant_keys)
        try:
            sub_instance, sub_report = schema_cls.from_dict_with_drift_check(view.raw)
        except Exception as exc:
            raise ConfigValidationError(
                file_name, view.validate_error_path, f"{check.validate_fail_label}: {exc}"
            ) from exc
        report.merge(view.drift_prefix, sub_report)
        view.backfill(_dump_without_none(sub_instance))


def _apply_file_validation(
    spec: _FileValidationSpec,
    instance: BaseConfig,
    raw_data: dict[str, Any],
    report: DriftReport,
) -> None:
    """统一校验分支：按域声明依次执行封闭集校验 → 物化补缺 → 在册段校验回填。"""
    if spec.enabled is not None:
        _check_closed_set(spec.file_name, spec.enabled, instance, raw_data)
    if spec.unknown_sections is not None:
        _check_closed_set(spec.file_name, spec.unknown_sections, instance, raw_data)
    if spec.materialize_registry is not None:
        _materialize_missing_sections(spec, instance, report)
    if spec.sections is not None:
        _validate_registry_sections(spec.file_name, spec.sections, instance, report)


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
    spec = _FILE_VALIDATION_SPECS.get(file_name)
    if spec is not None:
        _apply_file_validation(spec, instance, raw_data, report)
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
    from src.modules.component_catalog import ensure_component_registry

    ensure_component_registry()

    generated = _ensure_required_files(config_dir)
    if generated:
        logger.info(f"[加载管线] 首启生成 {len(generated)} 个缺失文件: {generated}")

    # --- 阶段① read_all_raw ---
    raw_docs: dict[str, dict[str, Any]] = {fname: _read_toml_dict(config_dir / fname) for fname in CONFIG_FILE_NAMES}
    logger.info(f"[加载管线] 阶段① read_all_raw 完成（{len(CONFIG_FILE_NAMES)} 文件）")

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
    for fname in CONFIG_FILE_NAMES:
        instance, report = _validate_file(fname, raw_docs[fname])
        validated[fname] = (instance, report)
    logger.info(f"[加载管线] 阶段④ Pydantic 校验完成（{len(CONFIG_FILE_NAMES)} 文件，硬错语义）")

    # --- 字段级冗余全域硬错 ---
    # typed 域的冗余键（拼错的字段名）与动态段未知键同属"文件里程序
    # 不认识的条目"：静默剥离 = 用户数据丢失（段级丢失事故的字段级
    # 形态），统一当面拦下。聚合全部文件一次报全，用户一次清完；
    # 键改名/退役场景由迁移钩子在阶段②③先行搬走，走到这里的冗余
    # 只可能是钩子欠账或手写笔误。
    redundant_findings: list[str] = []
    for fname, (_instance, report) in validated.items():
        redundant_findings.extend(f"{fname}: {r}" for r in report.redundant)
    if redundant_findings:
        first_file, first_path = redundant_findings[0].split(": ", 1)
        raise ConfigValidationError(
            first_file,
            first_path,
            f"配置存在程序不认识的字段（冗余键）：{redundant_findings}。"
            f"若为手写笔误请修正或删除；若因程序升级导致，请为旧键补迁移钩子",
        )

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
        scope = CONFIG_FILE_TO_SCOPE[fname]
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
