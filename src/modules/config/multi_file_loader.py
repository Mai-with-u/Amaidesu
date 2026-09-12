"""多文件配置加载器（阶段化管线）

从 config/ 目录加载 6 个 TOML 配置文件，按固定阶段序执行：

    ① read_all_raw      6 文件全读为 dict（缺失文件先行按 Schema 生成）
    ②③ 版本推进 + 升级钩子  每文件链跑区间钩子；跨文件钩子双写 + 双版本同升
    ④ Pydantic 校验     硬错——类型违约直接抛出（含字段 dotted path）
    ⑤ 漂移写回          全量写出 + 备份；采集器子段按注册表校验与补全
    ⑥ 合并视图          剥离 per-file ``[meta]`` 后按 scope 合并

配置文件结构（按域划分）:
    config/agents.toml      - 业务 Agent（含主播人设/上下文/后台维护段）
    config/collectors.toml  - 采集器（enabled 名单 + 各采集器段）
    config/tools.toml       - 工具提供者启用/配置
    config/model.toml       - LLM/VLM 模型配置（三层：providers/models/profiles）
    config/storage.toml     - 存储（顶层 [sqlite] + [memory]）
    config/infra.toml       - 基础设施（tts/subtitle/events/interceptors/dashboard/logging/simulator）

设计约定：
- **校验失败必须硬错**——不存在 raw dict 降级路径；加载失败由上层
  （ConfigService）翻译为启动失败。
- **meta 隔离**——每文件的 ``[meta]`` 段是文件私有元数据，合并视图装入前
  剥离；版本按文件独立读取（``get_config_version``）。
- **free-form 子段的权威在组件包**——采集器子段经组件注册表
  （``COMPONENT_SCHEMAS``）分发校验；TTS 引擎/字幕后端子段为 free-form
  dict，其编辑链路由 WebUI 侧的 provider Schema 承担，加载管线不做补全。
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
from src.modules.config.collectors_schemas import CollectorsRootConfig
from src.modules.config.tools_schemas import ToolsRootConfig
from src.modules.config.errors import ConfigValidationError
from src.modules.config.model_schemas import REQUIRED_PROFILE_NAMES, ModelConfig, ModelRootConfig
from src.modules.config.storage_schemas import StorageRootConfig
from src.modules.config.infra_schemas import InfraRootConfig
from src.modules.config.self_write_guard import mark_self_write
from src.modules.logging import get_logger

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
    """
    table = tomlkit.table()
    for sub_name, sub_info in type(instance).model_fields.items():
        value = getattr(instance, sub_name)
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
        # 嵌套表（BaseModel / AoT）由 tomlkit 渲染到父段之后，行内注释会
        # 悬空在后续标量字段之前——仅标量与 free-form dict 字段加注释
        is_nested_table = isinstance(value, BaseModel) or (
            isinstance(value, list) and value and all(isinstance(v, BaseModel) for v in value)
        )
        if sub_info.description and not is_nested_table:
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

    - 子段名 / enabled 名不在注册表 → 硬错（Typo 防护，列出合法名单）
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
            raise ConfigValidationError(
                "collectors.toml",
                "collectors.enabled",
                f"未注册的采集器名 {enabled_name!r}（合法名单：{known}）",
            )

    extras = root_instance.__pydantic_extra__ or {}
    for name in sorted(extras):
        schema_cls = COMPONENT_SCHEMAS.get(name)
        if schema_cls is None:
            raise ConfigValidationError(
                "collectors.toml",
                f"collectors.{name}",
                f"未注册的采集器段（合法名单：{known}）；新采集器需在其包内定义 ConfigSchema 并登记注册表",
            )
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


def _validate_required_llm_profiles(instance: BaseConfig) -> None:
    """``[llm_profiles]`` 必填 6 成员校验（加载期，缺失即硬错）"""
    profiles = getattr(instance, "llm_profiles", None)
    if not isinstance(profiles, dict):
        return
    missing = [name for name in REQUIRED_PROFILE_NAMES if name not in profiles]
    if missing:
        raise ConfigValidationError(
            "model.toml",
            "llm_profiles",
            f"缺少必填用途 profile：{missing}（必填 6 成员：{list(REQUIRED_PROFILE_NAMES)}）",
        )
    for name in REQUIRED_PROFILE_NAMES:
        if not profiles[name].model_list:
            raise ConfigValidationError(
                "model.toml",
                f"llm_profiles.{name}.model_list",
                "model_list 为空（至少引用 1 个模型名）",
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
    if isinstance(instance, ModelRootConfig):
        _validate_required_llm_profiles(instance)
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
    logger.info("[加载管线] 阶段① read_all_raw 完成（6 文件）")

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
    logger.info("[加载管线] 阶段④ Pydantic 校验完成（6 文件，硬错语义）")

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
