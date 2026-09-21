"""配置适配服务层

从六文件根 Schema 派生前端分组视图，并承担配置写路径的统一编排：
前置校验（未知项 / 只读 / 占位回写 / 空键）→ 按文件分组走统一管线写盘 →
按 scope 触发热重载。HTTP 语义（状态码、响应包装）留在 api 层。
"""

from collections import defaultdict
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence, Tuple, Union, get_args, get_origin

from pydantic import BaseModel

from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import (
    resolve_root_schema,
    update_config_values,
    validate_config_updates,
)
from src.modules.config.registry import COMPONENT_SCHEMAS, TOOL_PROVIDER_DOMAINS, TOOL_PROVIDER_SCHEMAS
from src.modules.config.schema_generator import ConfigSchemaGenerator, collect_all_fields
from src.modules.config.tools_schemas import ToolsConfig
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.config.service import ConfigService

logger = get_logger("ConfigAdapter")

# 六文件 scope 清单（= 文件名去后缀；顺序即 Schema 分组展示顺序）
_SCOPES = ("agents", "collectors", "tools", "avatar", "model", "storage", "infra")

# GET 响应中敏感字段的占位文案：明文不下发，回写同值会被拒绝
_SENSITIVE_PLACEHOLDER = "已设置"

_SENSITIVE_PATTERNS = ["api_key", "api_secret", "token", "password", "secret", "access_key_secret"]

_GEN_TYPE_MAP = {
    "number": "float",
    "boolean": "boolean",
    "integer": "integer",
    "string": "string",
    "select": "select",
    "array": "array",
    "object": "object",
}


# schema 树行走


def _unwrap_optional(annotation: Any) -> Any:
    """剥离 ``Optional[X]`` / ``Union[X, None]`` 包装，保留其它 ``Union`` 结构。"""
    if get_origin(annotation) is Union:
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _walk_schema(model_cls: type[BaseModel], parts: list[str]) -> Optional[tuple[str, Any]]:
    """在根 Schema 字段树中按段行走。

    返回值三态：
    - ``("leaf", (宿主模型, 字段定义))``：叶子字段定位成功
    - ``("free_dict", None)``：进入自由字典段，子键形状未知（跳过字段级校验）
    - ``None``：路径不存在（未知配置项）
    """
    if not parts:
        return None
    segment, rest = parts[0], parts[1:]

    extra_allow = model_cls.model_config.get("extra") == "allow"
    if segment not in model_cls.model_fields:
        # extra="allow" 的宿主（collectors 根）：未知段是采集器子段，
        # 权威 Schema 在组件注册表中，按注册表继续下钻
        if extra_allow and rest:
            sub_cls = COMPONENT_SCHEMAS.get(segment)
            if sub_cls is not None:
                return _walk_schema(sub_cls, rest)
        return None

    field_info = model_cls.model_fields[segment]
    if not rest:
        return ("leaf", (model_cls, field_info))

    annotation = _unwrap_optional(field_info.annotation)
    origin = get_origin(annotation)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _walk_schema(annotation, rest)
    if origin is dict:
        elem_args = get_args(annotation)
        elem = _unwrap_optional(elem_args[-1]) if elem_args else None
        if isinstance(elem, type) and issubclass(elem, BaseModel):
            # tools 动态分类域（avatar/studio/web，清单见注册表）：rest[0] 是
            # 提供者名，其 config 子段的权威 Schema 在工具提供者注册表——
            # 按注册表下钻做字段级校验
            if model_cls is ToolsConfig and segment in TOOL_PROVIDER_DOMAINS and len(rest) >= 2 and rest[1] == "config":
                provider_schema = TOOL_PROVIDER_SCHEMAS.get((segment, rest[0]))
                if provider_schema is not None:
                    deeper = rest[2:]
                    if not deeper:
                        # config 整对象更新：字段级不拆分，交由加载器整体校验
                        return ("free_dict", None)
                    return _walk_schema(provider_schema, deeper)
            # dict[str, 子模型]：rest[0] 是动态键（如 planner / mcp server 名），
            # 不参与字段校验，跳过后继续走值类型
            return _walk_schema(elem, rest[1:])
        return ("free_dict", None)
    return None


def _resolve_schema_node(key: str) -> Optional[tuple[str, Any]]:
    """按 scope 前缀 + 点分路径走 schema 树；结果语义见 ``_walk_schema``。"""
    parts = key.split(".")
    if len(parts) < 2 or not parts[0]:
        return None
    root_cls = resolve_root_schema(parts[0])
    if root_cls is None:
        return None
    return _walk_schema(root_cls, parts[1:])


def _field_is_readonly(field_info: Any) -> bool:
    """检查字段是否标记 readonly（来自 ``json_schema_extra={"readonly": True}``）。"""
    extra = getattr(field_info, "json_schema_extra", None)
    if isinstance(extra, dict) and extra.get("readonly") is True:
        return True
    return False


def _resolve_scope(key: str) -> str:
    """从点分 key 解析 scope（首段），例如 ``infra.dashboard.port`` → ``infra``。"""
    return key.split(".", 1)[0] if key else ""


def _find_empty_key(value: Any, path: str = "") -> Optional[str]:
    """递归查找 value 中嵌套的空 key（空字符串或纯空白），返回其字段路径；无则返回 None。

    TOML 序列化遇到空 key 会抛笼统错误，无法定位具体字段；
    此检查在写入前提前拦截，返回可定位的路径。
    """
    if isinstance(value, dict):
        for k, v in value.items():
            key_str = str(k)
            key_path = f"{path}.{key_str}" if path else key_str
            if not key_str.strip():
                return key_path
            sub = _find_empty_key(v, key_path)
            if sub is not None:
                return sub
    elif isinstance(value, list):
        for i, v in enumerate(value):
            sub = _find_empty_key(v, f"{path}[{i}]" if path else f"[{i}]")
            if sub is not None:
                return sub
    return None


# 脱敏与占位契约（读写共用）


def _is_sensitive_field(key: str) -> bool:
    key_lower = key.lower()
    # *_tokens（复数）是预算类参数（max_tokens 等），不是凭据——
    # "token" 模式会误伤它们，导致 UI 上显示占位且占位回写被拒
    if key_lower.endswith("_tokens"):
        return False
    return any(p in key_lower for p in _SENSITIVE_PATTERNS)


def _mask_sensitive_values(config: dict, path_prefix: str = "") -> dict:
    """递归遍历 config，对敏感字段的标量值替换为"已设置"占位，返回新 dict（不修改原对象）。

    路径语义与 schema 字段的 dotted key 一致：dict 子段拼接到前缀后，
    list 子项不引入新的路径段（数组元素匿名）。
    """
    masked: dict = {}
    for k, v in config.items():
        full_key = f"{path_prefix}.{k}" if path_prefix else str(k)
        if isinstance(v, dict):
            masked[k] = _mask_sensitive_values(v, full_key)
        elif isinstance(v, list):
            masked[k] = [_mask_sensitive_values(item, full_key) if isinstance(item, dict) else item for item in v]
        else:
            # 凭据均为字符串：数字/布尔值（如 token_budget_per_hour 预算参数）无论键名
            # 都不遮蔽——数字被替换成占位文本既不可读，还会撑爆前端的数字输入控件
            masked[k] = _SENSITIVE_PLACEHOLDER if isinstance(v, str) and _is_sensitive_field(full_key) else v
    return masked


def _find_placeholder_write(value: Any, path: str = "") -> Optional[str]:
    """递归查找回写进 value 的敏感占位符，返回其字段路径；无则返回 None。

    占位符本意是"真实值不下发"的 GET 展示形态；出现在写请求里意味着
    前端把占位当值回传，落盘会用占位文本覆盖真实凭据，必须拒绝。
    """
    if isinstance(value, dict):
        for k, v in value.items():
            key_str = str(k)
            key_path = f"{path}.{key_str}" if path else key_str
            if not isinstance(v, (dict, list)) and _is_sensitive_field(key_path) and v == _SENSITIVE_PLACEHOLDER:
                return key_path
            sub = _find_placeholder_write(v, key_path)
            if sub is not None:
                return sub
    elif isinstance(value, list):
        for v in value:
            sub = _find_placeholder_write(v, path)
            if sub is not None:
                return sub
    return None


def _check_writable(key: str, value: Any) -> Optional[str]:
    """写路径共用前置校验；通过返回 None，失败返回中文错误消息。

    覆盖：未知配置项（schema 树外）/ 只读字段 / 敏感字段占位回写 / 嵌套空键。
    值本身的类型与约束校验交给统一管线的 Schema 校验（写盘前硬错）。
    """
    empty_key_path = _find_empty_key(value)
    if empty_key_path is not None:
        return f"配置值包含空键: '{empty_key_path}'（位于 {key}，请移除空白键后重试）"

    placeholder_path = _find_placeholder_write(value)
    if placeholder_path is not None:
        return f"{placeholder_path} 的值为占位符（真实值不下发）；请输入新值，显式清空请提交空字符串"

    schema_node = _resolve_schema_node(key)
    if schema_node is None:
        return f"未知配置项: {key}"
    kind, payload = schema_node
    if kind == "free_dict":
        return None
    _containing_cls, leaf_field = payload
    if leaf_field is not None and _field_is_readonly(leaf_field):
        return f"{key} 为只读字段，禁止修改"
    return None


# 前端分组视图构建


def _get_nested_value(config: dict, dotted_key: str) -> Any:
    keys = dotted_key.split(".")
    current = config
    for k in keys:
        if isinstance(current, dict) and k in current:
            current = current[k]
        else:
            return None
    return current


def _map_gen_type(gen_type: str) -> str:
    return _GEN_TYPE_MAP.get(gen_type, "string")


def _extract_label(field: dict) -> str:
    label = field.get("label", "")
    if isinstance(label, dict):
        return label.get("zh_CN", label.get("en", "")) or field.get("name", "")
    if isinstance(label, str):
        return label
    return field.get("name", "")


def _display_value(key: str, main_config: dict) -> Any:
    """取字段当前值用于展示：剥 scope 前缀后在扁平化 main_config 中寻址。"""
    path_in_file = key.split(".", 1)[1] if "." in key else key
    return _get_nested_value(main_config, path_in_file)


def _convert_to_api_field(field: dict, main_config: dict) -> dict:
    """将 generator schema 的 field dict 转换为前端 API 字段格式。

    集中维护字段规范化规则，供分组构建复用。

    敏感字段：``value`` 一律返回"已设置"占位，避免明文 API key 通过 schema
    接口泄漏；前端只在用户真正编辑时提交新值（回写占位会被 422 拒绝），
    显式清空请提交空字符串。
    """
    dotted_key = field.get("key", "")
    raw_value = _display_value(dotted_key, main_config)
    is_sensitive = _is_sensitive_field(dotted_key)
    if isinstance(raw_value, list):
        # 对象数组整值返回：元素内敏感字符串按完整路径递归遮蔽（与 GET /config 同规则）
        scope, _, path_in_file = dotted_key.partition(".")
        raw_value = _mask_sensitive_values({path_in_file: raw_value}, scope)[path_in_file]
    gfield: dict = {
        "key": dotted_key,
        "label": _extract_label(field),
        "description": field.get("description", ""),
        "type": _map_gen_type(field.get("type", "string")),
        "default": field.get("default"),
        # 占位只针对字符串凭据：数字预算参数（如 token_budget_per_hour）照实返回，
        # 占位文本会撑爆前端的数字输入控件
        "value": _SENSITIVE_PLACEHOLDER if is_sensitive and isinstance(raw_value, str) else raw_value,
        "required": field.get("required", False),
        "sensitive": is_sensitive,
        # 透传 schema_generator 从 json_schema_extra 解析出的 readonly 标记，与写接口的拒绝逻辑共用同一信号
        "readonly": bool(field.get("readonly", False)),
    }
    validation: dict = {}
    for k in ("minValue", "maxValue", "options", "pattern"):
        if k in field:
            target = "min" if k == "minValue" else ("max" if k == "maxValue" else k)
            validation[target] = field[k]
    if validation:
        gfield["validation"] = validation
    if field.get("items"):
        items_schema = field["items"]
        if isinstance(items_schema, dict) and items_schema.get("type") == "object":
            # 元素子字段树同样走规范化（label/validation 等），key 为元素内相对字段名，
            # 前端据此在数组元素对象内寻址
            sub_fields = []
            for sub in items_schema.get("fields", []) or []:
                sub_name = str(sub.get("name", ""))
                sub_converted = _convert_to_api_field({**sub, "key": f"{dotted_key}.{sub_name}"}, {})
                sub_converted["key"] = sub_name
                sub_fields.append(sub_converted)
            gfield["items"] = {"type": "object", "fields": sub_fields}
        else:
            gfield["items"] = items_schema
    return gfield


def _group_into_children(fields: list[dict], _depth: int = 1) -> list[dict]:
    """将扁平的点分 key 字段列表构建为层级 children 结构。

    ``_depth`` 标记当前层在 dotted key 中的段索引（初始 1 = scope 后第一位）。
    递归时 ``_depth + 1``，不需要改动 key 本身。

    注意：同一 key 同时有扁平字段和嵌套子字段时（如 ``message_config`` 既是
    ``type="object"`` 又是更深层字段的父级），只保留容器（children），丢弃扁平字段，
    避免前端渲染出空卡片或 ``[object Object]``。
    """
    nxt = _depth + 1
    grouped: dict[str, list[dict]] = defaultdict(list)
    for field in fields:
        parts = field.get("key", "").split(".")
        if len(parts) >= nxt + 1:
            grouped[parts[_depth]].append(field)

    # 先构建所有容器，记录容器 key
    containers: list[dict] = []
    for comp_name, comp_fields in sorted(grouped.items()):
        children = _group_into_children(comp_fields, _depth + 1)
        # 从上溯链推导容器 key（stable，不依赖组内第一个字段）
        _prefix_candidates = [f.get("key", "").split(".")[:_depth] for f in comp_fields if f.get("key")]
        _prefix = _prefix_candidates[0] if _prefix_candidates else [comp_name]
        container_key = ".".join(_prefix + [comp_name])
        label = comp_name.replace("_", " ").title()
        containers.append(
            {
                "key": container_key,
                "label": label,
                "description": "",
                "type": "object",
                "default": None,
                "value": None,
                "required": False,
                "sensitive": False,
                "children": children,
            }
        )

    container_keys = {c["key"] for c in containers}

    # 扁平字段：排除同时是容器的 key（避免重复渲染）
    result = [f for f in fields if len(f.get("key", "").split(".")) == nxt and f.get("key") not in container_keys]

    result.extend(containers)
    return result


def _build_frontend_groups(config_service: "ConfigService") -> dict:
    """Schema 适配器：六文件根 Schema → {groups, version} 前端格式.

    每个根 Schema 一个分组；分组 label / 文件归属来自根类的自描述协议
    （``__section_label__`` / ``__file_name__``），无手写映射表。
    字段 key 在文件内路径前加 scope 前缀，与合并视图寻址一致。
    """

    main_config = config_service.main_config or {}

    groups: list[dict] = []
    for scope in _SCOPES:
        root_cls = resolve_root_schema(scope)
        if root_cls is None:
            continue
        schema = ConfigSchemaGenerator.generate_config_schema(root_cls)
        collected = collect_all_fields(schema)
        leaf_fields = [f for f in collected if "." in str(f.get("key", ""))]
        # 对象数组是一等可编辑字段（元素子字段树在 items.fields），与叶子一起进组
        leaf_fields.extend(f for f in collected if f.get("type") == "array")
        group_fields: list[dict] = []
        for field in leaf_fields:
            # 文件内路径 → scope 前缀的 API 键
            field["key"] = f"{scope}.{field['key']}"
            group_fields.append(_convert_to_api_field(field, main_config))
        group_fields = _group_into_children(group_fields)

        groups.append(
            {
                "key": scope,
                "label": root_cls.__section_label__ or scope,
                "description": "",
                "icon": None,
                "order": 99,
                "fields": group_fields,
                "file_name": root_cls.__file_name__,
                "file_label": root_cls.__section_label__ or root_cls.__file_name__,
            }
        )

    return {"groups": groups, "version": "1.0.0"}


# 写路径编排


@dataclass
class ConfigApplyOutcome:
    """一次配置写请求的编排结果（api 层负责翻译为 HTTP 响应）。"""

    success: bool
    message: str = ""
    requires_restart: bool = False
    # 单文件批次时携带实际写入的 TOML 文件名，供响应透出
    target_file: Optional[str] = None
    # last-wins 去重后的实际写入条目（key, value），供响应逐条回执
    applied: list[tuple[str, Any]] = dataclasses.field(default_factory=list)
    errors: list[Tuple[str, str]] = dataclasses.field(default_factory=list)


async def _apply_and_reload(
    config_service: "ConfigService",
    config_dir: Path,
    updates_by_file: dict[str, dict[str, Any]],
    keys: list[str],
) -> tuple[bool, bool, Optional[str]]:
    """按文件分组走统一管线写盘，随后对触碰的 scope 尝试重载。

    Returns:
        (success, requires_restart, error_message)
    """
    # 事务前置：全部文件先校验，任一失败即整批拒绝（磁盘零写入）
    try:
        for file_name, updates in updates_by_file.items():
            validate_config_updates(config_dir, file_name, updates)
    except ConfigValidationError as e:
        return False, False, str(e)

    for file_name, updates in updates_by_file.items():
        try:
            update_config_values(config_dir, file_name, updates)
        except ConfigValidationError as e:
            # 前置校验已通过，此处失败属并发修改窗口；忠实上报
            logger.error(f"写入配置文件失败: {file_name}: {e}")
            return False, False, f"写入配置文件失败: {e}"

    scopes = [_resolve_scope(k) for k in keys]
    reloaded = await config_service.reload_config(changed_scopes=scopes)
    hot_applied = bool(reloaded) and all(s == "infra" for s in scopes)
    return True, not hot_applied, None


def _fill_array_placeholders(key: str, value: Any, main_config: dict) -> Any:
    """对象数组整值提交的占位回填。

    前端整列表提交时，元素内未编辑的敏感字段仍是 GET 下发的"已设置"占位
    （前端不持有真实凭据）。按索引对齐磁盘现值回填真实值，占位文本才不会
    落盘覆盖凭据。回填后仍残留占位（如新元素未填写）交由占位写检查拒绝。
    """
    if not isinstance(value, list):
        return value
    path_in_file = key.split(".", 1)[1] if "." in key else key
    current = _get_nested_value(main_config, path_in_file)
    if not isinstance(current, list):
        return value
    filled: list[Any] = []
    for index, new_item in enumerate(value):
        # 磁盘越界（新增元素）原样保留：占位交由占位写检查拒绝，不能静默丢弃元素
        old_item = current[index] if index < len(current) else None
        filled.append(_restore_masked(new_item, old_item) if isinstance(new_item, dict) else new_item)
    return filled


def _restore_masked(new_item: dict, old_item: Any) -> dict:
    """递归恢复 new_item 中值为占位符的敏感字段（取 old_item 同名真实值）。"""
    restored: dict = {}
    for field_name, field_value in new_item.items():
        old_value = old_item.get(field_name) if isinstance(old_item, dict) else None
        if field_value == _SENSITIVE_PLACEHOLDER and old_value is not None:
            restored[field_name] = old_value
        elif isinstance(field_value, dict):
            restored[field_name] = _restore_masked(field_value, old_value)
        else:
            restored[field_name] = field_value
    return restored


async def apply_config_updates(
    config_service: "ConfigService",
    config_dir: Path,
    changes: Sequence[Tuple[str, Any]],
) -> ConfigApplyOutcome:
    """配置写路径统一业务入口（单条与批量共用，重复 key 按 last-wins 去重）。

    编排顺序：逐条前置校验 → 按 scope 分组到目标 TOML 文件 → 统一管线写盘 →
    触碰 scope 热重载。任一前置校验失败即整体拒绝，磁盘零写入。
    """
    if not changes:
        return ConfigApplyOutcome(success=False, message="没有可保存的更改")

    deduped: Dict[str, Any] = {}
    for key, value in changes:
        deduped[key] = value
    main_config = config_service.main_config or {}
    normalized: list[tuple[str, Any]] = [
        (key, _fill_array_placeholders(key, value, main_config)) for key, value in deduped.items()
    ]

    errors: list[tuple[str, str]] = []
    for key, value in normalized:
        check_error = _check_writable(key, value)
        if check_error is not None:
            errors.append((key, check_error))
    if errors:
        first_key, first_message = errors[0]
        message = first_message if len(errors) == 1 else f"{first_message}（另有 {len(errors) - 1} 项失败）"
        return ConfigApplyOutcome(success=False, message=message, errors=list(errors))

    updates_by_file: dict[str, dict[str, Any]] = {}
    for key, value in normalized:
        root_cls = resolve_root_schema(_resolve_scope(key))
        if root_cls is None:
            # 前置校验已覆盖未知 scope，此分支仅防御非法到达的键
            detail = f"未知配置项: {key}"
            return ConfigApplyOutcome(success=False, message=detail, errors=[(key, detail)])
        path_in_file = key.split(".", 1)[1]
        updates_by_file.setdefault(root_cls.__file_name__, {})[path_in_file] = value

    target_file = next(iter(updates_by_file)) if len(updates_by_file) == 1 else None
    success, requires_restart, error = await _apply_and_reload(
        config_service, config_dir, updates_by_file, [k for k, _ in normalized]
    )
    if not success:
        return ConfigApplyOutcome(success=False, message=error or "写入失败")
    return ConfigApplyOutcome(
        success=True, requires_restart=requires_restart, target_file=target_file, applied=normalized
    )
