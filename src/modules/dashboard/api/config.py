"""配置管理 API（六文件树）

提供配置的查询、Schema 获取和修改接口。

配置布局为 v2 六文件树（``config/agents.toml`` / ``collectors.toml`` /
``tools.toml`` / ``model.toml`` / ``storage.toml`` / ``infra.toml``），
文件归属与显示名由各根 Schema 的自描述协议（``__file_name__`` /
``__section_label__``）提供，本模块不维护任何手写映射表。

API 键约定：**scope 前缀 + 文件内点分路径**——``tools.tools.tasks.poll_interval_ms``、
``agents.agents.streamer.persona.bot_name``、``infra.dashboard.port``。
首个段（scope）路由到对应文件；剥掉前缀的文件内路径同时是 GET 返回的
扁平化合并视图（``main_config``）的寻址方式。

校验语义：写路径统一走加载管线的 Schema 校验（``update_config_values``），
类型/约束违约返回 422 + 中文消息（含文件名与字段路径），无手写字段校验分支。
"""

from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Dict, Optional, Union, get_args, get_origin
import asyncio
import os
import subprocess
import sys

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import (
    resolve_root_schema,
    update_config_values,
    validate_config_updates,
)
from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("ConfigAPI")

# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]

# 六文件 scope 清单（= 文件名去后缀；顺序即 Schema 分组展示顺序）
_SCOPES = ("agents", "collectors", "tools", "model", "storage", "infra")

# GET 响应中敏感字段的占位文案：明文不下发，回写同值会被拒绝
_SENSITIVE_PLACEHOLDER = "已设置"


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
            from src.modules.config.registry import COMPONENT_SCHEMAS

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


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ConfigResponse(BaseModel):
    """完整配置响应 (合并视图)

    顶层键为六个 scope：agents / collectors / tools / model / storage / infra。
    """

    config: Dict[str, Any] = Field(default_factory=dict, description="完整配置字典")


class ConfigUpdateRequest(BaseModel):
    """配置更新请求"""

    key: str = Field(description="配置键（scope 前缀点分路径，如 'infra.dashboard.port'）")
    value: Any = Field(description="配置值")


class ConfigUpdateResponse(BaseModel):
    """配置更新响应"""

    success: bool = Field(description="是否成功")
    message: str = Field(description="结果消息")
    requires_restart: bool = Field(default=False, description="是否需要重启服务")
    target_file: Optional[str] = Field(
        default=None,
        description="实际写入的 TOML 文件名 (用于调试与排错)",
    )


class BatchConfigChange(BaseModel):
    """批量更新中的单条变更。"""

    key: str = Field(description="配置键（scope 前缀点分路径，如 'tools.tools.tasks.poll_interval_ms'）")
    value: Any = Field(description="配置值")


class BatchConfigUpdateRequest(BaseModel):
    """批量配置更新请求。

    多个变更按提交顺序处理：
    - 同一批次内出现重复 key 时，后者覆盖前者的最终写入值（last-wins），
      这是为了支持前端"反复编辑同字段"时的最终一致性，不视为错误。
    - 整体按事务处理：任意一条校验失败则整个批次回退（无文件被改写）。
    """

    changes: list[BatchConfigChange] = Field(description="本次要提交的变更列表（按顺序处理，重复 key 后者覆盖前者）")


class BatchChangeResult(BaseModel):
    """批量端点中每条变更的处理结果。"""

    key: str = Field(description="配置键")
    success: bool = Field(description="本条是否成功")


class BatchChangeError(BaseModel):
    """批量端点中失败条目的错误明细。"""

    key: str = Field(description="失败的配置键")
    message: str = Field(description="失败原因（中文）")


class BatchConfigUpdateResponse(BaseModel):
    """批量配置更新响应。

    - 全部成功时：``success=true``，``results`` 列出每条 key 与 success=true，
      非 hot 段变更附带 ``requires_restart=true``。
    - 任一失败时：``success=false``，``errors`` 列出失败条目，``message`` 是首条失败的
      中文消息（含 "（另有 N 项失败）" 聚合后缀），并保证磁盘零写入。
    """

    success: bool = Field(description="是否全部成功")
    message: str = Field(description="聚合后的结果消息")
    requires_restart: bool = Field(default=False, description="是否需要重启服务（仅全部成功时为 true）")
    results: list[BatchChangeResult] = Field(
        default_factory=list,
        description="每条变更的处理结果（仅成功时填充）",
    )
    errors: list[BatchChangeError] = Field(
        default_factory=list,
        description="失败条目列表（仅失败时填充）",
    )


# ---------------------------------------------------------------------------
# GET /api/v1/config
# ---------------------------------------------------------------------------


@router.get("", response_model=ConfigResponse)
async def get_config(server: ServerDep) -> ConfigResponse:
    """获取当前配置

    返回扁平化的 ``main_config``（六文件 scope 展平后的合并视图，键为
    文件内点分路径的首段，如 ``agents`` / ``tools`` / ``dashboard``）。

    敏感字段 (api_key / token / password / secret 等) 的值在响应中替换为
    "已设置" 占位——明文凭据不从此接口导出；回写该占位值会被 422 拒绝，
    真正清空请显式提交空字符串。
    前端配置页应通过 ``/api/v1/config/schema`` 读取字段定义，
    本接口仅作为只读快照使用。
    """
    config_service = server.config_service
    if not config_service:
        logger.warning("Config service 不可用,返回空配置")
        return ConfigResponse()

    try:
        raw_config = dict(config_service.main_config or {})
        masked_config = _mask_sensitive_values(raw_config)
        return ConfigResponse(config=masked_config)
    except Exception as e:
        logger.error(f"获取配置失败: {e}", exc_info=True)
        return ConfigResponse()


# ---------------------------------------------------------------------------
# GET /api/v1/config/schema
# ---------------------------------------------------------------------------


class SchemaGroupsResponse(BaseModel):
    """前端 groups 格式响应."""

    groups: list[Dict[str, Any]] = Field(default_factory=list, description="配置分组列表")
    version: str = Field(default="1.0.0", description="Schema 版本号")


def _get_nested_value(config: dict, dotted_key: str) -> Any:
    keys = dotted_key.split(".")
    current = config
    for k in keys:
        if isinstance(current, dict) and k in current:
            current = current[k]
        else:
            return None
    return current


_SENSITIVE_PATTERNS = ["api_key", "api_secret", "token", "password", "secret", "access_key_secret"]


def _is_sensitive_field(key: str) -> bool:
    key_lower = key.lower()
    # *_tokens（复数）是预算类参数（max_tokens 等），不是凭据——
    # "token" 模式会误伤它们，导致 UI 上显示占位且占位回写被拒
    if key_lower.endswith("_tokens"):
        return False
    return any(p in key_lower for p in _SENSITIVE_PATTERNS)


_GEN_TYPE_MAP = {
    "number": "float",
    "boolean": "boolean",
    "integer": "integer",
    "string": "string",
    "select": "select",
    "array": "array",
    "object": "object",
}


def _map_gen_type(gen_type: str) -> str:
    return _GEN_TYPE_MAP.get(gen_type, "string")


def _extract_label(field: dict) -> str:
    label = field.get("label", "")
    if isinstance(label, dict):
        return label.get("zh_CN", label.get("en", "")) or field.get("name", "")
    if isinstance(label, str):
        return label
    return field.get("name", "")


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
            masked[k] = _SENSITIVE_PLACEHOLDER if _is_sensitive_field(full_key) else v
    return masked


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
    gfield: dict = {
        "key": dotted_key,
        "label": _extract_label(field),
        "description": field.get("description", ""),
        "type": _map_gen_type(field.get("type", "string")),
        "default": field.get("default"),
        "value": _SENSITIVE_PLACEHOLDER if is_sensitive else raw_value,
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
        gfield["items"] = field["items"]
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


def _build_frontend_groups(config_service) -> dict:
    """Schema 适配器：六文件根 Schema → {groups, version} 前端格式.

    每个根 Schema 一个分组；分组 label / 文件归属来自根类的自描述协议
    （``__section_label__`` / ``__file_name__``），无手写映射表。
    字段 key 在文件内路径前加 scope 前缀，与合并视图寻址一致。
    """
    from src.modules.config.schema_generator import (
        ConfigSchemaGenerator,
        collect_all_fields,
    )

    main_config = config_service.main_config or {}

    groups: list[dict] = []
    for scope in _SCOPES:
        root_cls = resolve_root_schema(scope)
        if root_cls is None:
            continue
        schema = ConfigSchemaGenerator.generate_config_schema(root_cls)
        leaf_fields = [f for f in collect_all_fields(schema) if "." in str(f.get("key", ""))]
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


@router.get("/schema", response_model=SchemaGroupsResponse)
async def get_config_schema(server: ServerDep) -> SchemaGroupsResponse:
    """获取配置 Schema

    使用 ``ConfigSchemaGenerator`` 从六个根 Schema 自动推导，
    经 ``_build_frontend_groups`` 转换为前端 ``{groups, version}`` 格式。
    """
    config_service = server.config_service
    if not config_service:
        logger.warning("Config service 不可用,返回空 schema")
        return SchemaGroupsResponse()

    try:
        result = _build_frontend_groups(config_service)
        return SchemaGroupsResponse(**result)
    except Exception as e:
        logger.error(f"获取配置 Schema 失败: {e}", exc_info=True)
        return SchemaGroupsResponse()


# ---------------------------------------------------------------------------
# 写路径共用校验
# ---------------------------------------------------------------------------


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


async def _apply_and_reload(
    config_service,
    config_dir,
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


# ---------------------------------------------------------------------------
# PATCH /api/v1/config
# ---------------------------------------------------------------------------


@router.patch("", response_model=ConfigUpdateResponse)
async def update_config(request: ConfigUpdateRequest, server: ServerDep) -> ConfigUpdateResponse:
    """更新配置（写入对应 TOML 文件，经统一管线）

    根据 ``request.key`` 的 scope 首段路由到对应文件：
    - ``agents.*`` → ``agents.toml``
    - ``collectors.*`` → ``collectors.toml``
    - ``tools.*`` → ``tools.toml``
    - ``model.*`` → ``model.toml``
    - ``storage.*`` → ``storage.toml``
    - ``infra.*`` → ``infra.toml``（hot 段，写后即时重载生效）
    """
    config_service = server.config_service
    if not config_service:
        raise HTTPException(status_code=503, detail="Config service 不可用")

    scope = _resolve_scope(request.key)
    root_cls = resolve_root_schema(scope)
    if root_cls is None:
        raise HTTPException(status_code=422, detail=f"未知配置域: {scope!r}（合法 scope: {list(_SCOPES)}）")

    check_error = _check_writable(request.key, request.value)
    if check_error is not None:
        raise HTTPException(status_code=422, detail=check_error)

    file_name = root_cls.__file_name__
    path_in_file = request.key.split(".", 1)[1]
    config_dir = _get_config_dir(config_service)

    success, requires_restart, error = await _apply_and_reload(
        config_service, config_dir, {file_name: {path_in_file: request.value}}, [request.key]
    )
    if not success:
        raise HTTPException(status_code=422, detail=error or "写入失败")

    logger.info(f"配置已更新: {request.key} (写入 {file_name})")
    message = "配置已保存，hot 段已即时生效" if not requires_restart else "配置已保存到文件，需重启服务后完全生效"
    return ConfigUpdateResponse(
        success=True,
        message=message,
        requires_restart=requires_restart,
        target_file=file_name,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/config/batch — 批量原子保存
# ---------------------------------------------------------------------------


@router.post("/batch", response_model=BatchConfigUpdateResponse)
async def batch_update_config(
    request: BatchConfigUpdateRequest,
    server: ServerDep,
) -> BatchConfigUpdateResponse:
    """批量原子更新配置。

    单次请求携带多条变更，要么全部成功要么全部回退（事务语义）：
    任一变更在前置校验或 Schema 校验阶段失败则拒绝整个批次并保持磁盘零写入；
    写入阶段按目标 TOML 文件分组，每个文件只写一次（经统一管线，含备份与自写压标）。

    同一批次内出现重复 key 时按 **last-wins** 处理：后者的 value 覆盖前者的
    最终写入值，便于前端"反复编辑同字段后保存"的最终一致性，不视为错误。
    """
    config_service = server.config_service
    if not config_service:
        raise HTTPException(status_code=503, detail="Config service 不可用")

    if not request.changes:
        return BatchConfigUpdateResponse(success=False, message="没有可保存的更改")

    deduped: Dict[str, Any] = {}
    for change in request.changes:
        deduped[change.key] = change.value
    normalized: list[tuple[str, Any]] = list(deduped.items())

    errors: list[BatchChangeError] = []
    for key, value in normalized:
        check_error = _check_writable(key, value)
        if check_error is not None:
            errors.append(BatchChangeError(key=key, message=check_error))
    if errors:
        first = errors[0]
        agg = first.message if len(errors) == 1 else f"{first.message}（另有 {len(errors) - 1} 项失败）"
        return BatchConfigUpdateResponse(success=False, message=agg, errors=errors)

    updates_by_file: dict[str, dict[str, Any]] = {}
    for key, value in normalized:
        scope = _resolve_scope(key)
        root_cls = resolve_root_schema(scope)
        if root_cls is None:
            raise HTTPException(
                status_code=422,
                detail=f"未知配置域: {scope!r}（合法 scope: {list(_SCOPES)}）",
            )
        path_in_file = key.split(".", 1)[1]
        updates_by_file.setdefault(root_cls.__file_name__, {})[path_in_file] = value

    config_dir = _get_config_dir(config_service)
    success, requires_restart, error = await _apply_and_reload(
        config_service, config_dir, updates_by_file, [k for k, _ in normalized]
    )
    if not success:
        raise HTTPException(status_code=422, detail=error or "写入失败")

    logger.info(f"批量配置更新成功: 共 {len(normalized)} 项")
    return BatchConfigUpdateResponse(
        success=True,
        message="配置已保存" + ("，hot 段已即时生效" if not requires_restart else "，需重启服务后完全生效"),
        requires_restart=requires_restart,
        results=[BatchChangeResult(key=k, success=True) for k, _ in normalized],
    )


# ---------------------------------------------------------------------------
# POST /api/v1/config/restart
# ---------------------------------------------------------------------------


@router.post("/restart", response_model=ConfigUpdateResponse)
async def restart_service(server: ServerDep) -> ConfigUpdateResponse:
    try:
        logger.info("收到重启服务请求")

        async def _restart():
            await asyncio.sleep(0.5)
            subprocess.Popen(
                [sys.executable] + sys.argv,
                cwd=os.getcwd(),
                close_fds=True,
            )
            os._exit(0)

        asyncio.create_task(_restart())

        return ConfigUpdateResponse(
            success=True,
            message="正在重启服务...",
            requires_restart=False,
        )
    except Exception as e:
        logger.error(f"重启服务失败: {e}", exc_info=True)
        return ConfigUpdateResponse(
            success=False,
            message=f"重启服务失败: {str(e)}",
        )


def _get_config_dir(config_service) -> Any:
    """从 ConfigService 推导 config/ 目录路径。"""

    return Path(config_service.base_dir) / "config"
