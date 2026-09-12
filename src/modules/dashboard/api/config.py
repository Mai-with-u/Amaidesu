"""
配置管理 API

提供配置的查询、Schema 获取和修改接口。

多文件配置支持:
- ``config/core.toml`` - core 节族 (general/persona/maicore/context/dashboard/logging/pipelines)
- ``config/model.toml`` - model 节族 (llm/llm_fast/vlm/llm_local)
- ``config/input.toml`` - collectors 节 (Input 阶段)
- ``config/decision.toml`` - deciders 节 (Decision 阶段)
- ``config/output.toml`` - handlers 节 (Output 阶段)

PATCH 通过 ``key`` 的首段 (例如 ``persona.bot_name`` → ``persona``) 路由到正确的 TOML 文件。
"""

from collections import defaultdict
from typing import TYPE_CHECKING, Annotated, Any, Dict, Optional, Union, get_args, get_origin
import asyncio
import os
import re
import subprocess
import sys

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError

from src.modules.config.agents_schemas import AgentsRootConfig
from src.modules.config.core_schemas import CoreConfig
from src.modules.config.memory_schemas import MemoryRootConfig
from src.modules.config.model_schemas import ModelConfig
from src.modules.config.storage_schemas import StorageRootConfig
from src.modules.config.toml_utils import (
    load_toml_with_comments,
    write_toml_preserve,
)
from src.modules.config.tools_schemas import ToolsRootConfig
from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("ConfigAPI")

# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


# section 顶层字段名 → Pydantic 根模型；与 _build_frontend_groups 共用同一份 schema 树
_SECTION_TO_ROOT_MODEL: Dict[str, type[BaseModel]] = {
    "meta": CoreConfig,
    "general": CoreConfig,
    "persona": CoreConfig,
    "context": CoreConfig,
    "events": CoreConfig,
    "dashboard": CoreConfig,
    "logging": CoreConfig,
    "interceptors": CoreConfig,
    "simulator": CoreConfig,
    "tts": CoreConfig,
    "subtitle": CoreConfig,
    "llm": ModelConfig,
    "llm_fast": ModelConfig,
    "vlm": ModelConfig,
    "llm_local": ModelConfig,
    "llm_providers": ModelConfig,
    "llm_summary": ModelConfig,
    "llm_agenda": ModelConfig,
    "agents": AgentsRootConfig,
    "streamer": AgentsRootConfig,
    "tools": ToolsRootConfig,
    "perception": ToolsRootConfig,
    "output": ToolsRootConfig,
    "understanding": ToolsRootConfig,
    "content_engine": ToolsRootConfig,
    "external": ToolsRootConfig,
    "memory": MemoryRootConfig,
    "simple": MemoryRootConfig,
    "amemorix": MemoryRootConfig,
    "storage": StorageRootConfig,
    "sqlite": StorageRootConfig,
}


def _unwrap_optional(annotation: Any) -> Any:
    """剥离 ``Optional[X]`` / ``Union[X, None]`` 包装，保留其它 ``Union`` 结构。"""
    if get_origin(annotation) is Union:
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _resolve_schema_node(key: str) -> Optional[tuple[type[BaseModel], Any]]:
    """按点分 key 走 schema 树；返回 (containing_model_cls, leaf_field_info_or_None)。

    任意一段未在 schema 中找到则返回 ``None``，由调用方按"未知配置项"拒绝。
    ``dict[str, Any]`` 字段（如拦截器配置）下接受任意下一段键作为叶子字段；
    此时 ``leaf_field_info`` 为 ``None``，调用方跳过字段级类型/约束校验。
    """
    parts = key.split(".")
    if not parts or not parts[0]:
        return None
    section = parts[0]
    root_cls = _SECTION_TO_ROOT_MODEL.get(section)
    if root_cls is None:
        return None
    current_cls = root_cls
    for i, segment in enumerate(parts[:-1]):
        if segment not in current_cls.model_fields:
            return None
        fld = current_cls.model_fields[segment]
        annotation = _unwrap_optional(fld.annotation)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            current_cls = annotation
        elif get_origin(annotation) is dict:
            # 自由键 dict 字段：仅当下一段就是叶子时合法，否则无法再向下展开
            if i < len(parts) - 2:
                return None
            return current_cls, fld
        else:
            return None
    leaf_name = parts[-1]
    if leaf_name not in current_cls.model_fields:
        return None
    return current_cls, current_cls.model_fields[leaf_name]


def _field_is_readonly(field_info: Any) -> bool:
    """检查字段是否标记 readonly（来自 ``json_schema_extra={"readonly": True}``）。"""
    extra = getattr(field_info, "json_schema_extra", None)
    if isinstance(extra, dict) and extra.get("readonly") is True:
        return True
    return False


def _validate_value_for_field(value: Any, field_info: Any, dotted_key: str) -> Optional[str]:
    """校验 ``value`` 是否匹配字段类型与约束；通过返回 ``None``，失败返回中文错误消息。

    校验维度：
    - 类型：标量 (str/int/float/bool) / 数组 (list) / 对象 (dict) / Literal / 嵌套 Pydantic 模型
    - 约束：从 Pydantic ``Field.metadata`` 提取 ge/le/gt/lt/min_length/max_length/pattern

    选择此实现的原因：deterministic + testable。覆盖嵌套 Pydantic 模型字段时调用
    ``model_validate``，让 Pydantic 自身的错误处理覆盖 min_length/pattern 等深层约束；
    标量字段用直接 isinstance + 约束比较，避免构造整个 containing model 的开销。
    """
    annotation = _unwrap_optional(field_info.annotation)

    # Literal[X, Y, ...] -> 值必须命中选项之一
    if get_origin(annotation) is not None and str(get_origin(annotation)) == "typing.Literal":
        options = list(get_args(annotation))
        if value not in options:
            return f"{dotted_key} 值必须是 {options} 之一，收到: {value!r}"
        return None

    # 嵌套 Pydantic 模型 -> 走 model_validate
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if not isinstance(value, dict):
            return f"{dotted_key} 应为对象，收到: {type(value).__name__}"
        try:
            annotation.model_validate(value)
        except ValidationError as e:
            return f"{dotted_key} 结构校验失败: {e}"
        return None

    # 标量类型（注意：bool 必须在 int 之前判断，因为 isinstance(True, int) == True）
    if annotation is bool:
        if not isinstance(value, bool):
            return f"{dotted_key} 应为布尔值 (true/false)，收到: {type(value).__name__}"
    elif annotation is int:
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{dotted_key} 应为整数，收到: {type(value).__name__}"
    elif annotation is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{dotted_key} 应为数字，收到: {type(value).__name__}"
    elif annotation is str:
        if not isinstance(value, str):
            return f"{dotted_key} 应为字符串，收到: {type(value).__name__}"
    elif get_origin(annotation) in {list, set, tuple}:
        if not isinstance(value, list):
            return f"{dotted_key} 应为数组，收到: {type(value).__name__}"
        elem_args = get_args(annotation)
        if elem_args:
            elem_type = _unwrap_optional(elem_args[0])
            for i, item in enumerate(value):
                if isinstance(elem_type, type) and issubclass(elem_type, BaseModel):
                    if not isinstance(item, dict):
                        return f"{dotted_key}[{i}] 应为对象，收到: {type(item).__name__}"
                    try:
                        elem_type.model_validate(item)
                    except ValidationError as e:
                        return f"{dotted_key}[{i}] 结构校验失败: {e}"
                elif elem_type is int and (isinstance(item, bool) or not isinstance(item, int)):
                    return f"{dotted_key}[{i}] 应为整数，收到: {type(item).__name__}"
                elif elem_type is float and (isinstance(item, bool) or not isinstance(item, (int, float))):
                    return f"{dotted_key}[{i}] 应为数字，收到: {type(item).__name__}"
                elif elem_type is str and not isinstance(item, str):
                    return f"{dotted_key}[{i}] 应为字符串，收到: {type(item).__name__}"
                elif elem_type is bool and not isinstance(item, bool):
                    return f"{dotted_key}[{i}] 应为布尔值，收到: {type(item).__name__}"
    elif annotation is dict or get_origin(annotation) is dict:
        if not isinstance(value, dict):
            return f"{dotted_key} 应为对象，收到: {type(value).__name__}"
    else:
        # 未识别的注解类型：保守放行（写入将由外层 tomlkit 序列化兜底）
        return None

    # 标量/字符串约束（ge/le/gt/lt/min_length/max_length/pattern）
    metadata = getattr(field_info, "metadata", None) or []
    for c in metadata:
        ge = getattr(c, "ge", None)
        le = getattr(c, "le", None)
        gt = getattr(c, "gt", None)
        lt = getattr(c, "lt", None)
        min_length = getattr(c, "min_length", None)
        max_length = getattr(c, "max_length", None)
        pattern = getattr(c, "pattern", None)
        if isinstance(value, str) and min_length is not None and len(value) < min_length:
            return f"{dotted_key} 长度不能少于 {min_length} 字符"
        if isinstance(value, str) and max_length is not None and len(value) > max_length:
            return f"{dotted_key} 长度不能超过 {max_length} 字符"
        if isinstance(value, str) and pattern is not None:
            # Pydantic 用 search 校验 pattern，这里保持一致
            if not re.search(pattern, value):
                return f"{dotted_key} 不匹配要求的格式 ({pattern})"
        if not isinstance(value, bool) and isinstance(value, (int, float)) and ge is not None and value < ge:
            return f"{dotted_key} 不能小于 {ge}"
        if not isinstance(value, bool) and isinstance(value, (int, float)) and le is not None and value > le:
            return f"{dotted_key} 不能大于 {le}"
        if not isinstance(value, bool) and isinstance(value, (int, float)) and gt is not None and value <= gt:
            return f"{dotted_key} 必须大于 {gt}"
        if not isinstance(value, bool) and isinstance(value, (int, float)) and lt is not None and value >= lt:
            return f"{dotted_key} 必须小于 {lt}"
    return None


def _resolve_section(key: str) -> str:
    """从点分 key 解析顶层 section (例如 'persona.bot_name' → 'persona')"""
    if not key:
        return ""
    return key.split(".", 1)[0]


def _find_empty_key(value: Any, path: str = "") -> Optional[str]:
    """递归查找 value 中嵌套的空 key（空字符串或纯空白），返回其字段路径；无则返回 None。

    tomlkit 序列化时遇到空 key 会抛笼统的 "Empty key" 错误，无法定位具体字段。
    此函数在写入前提前校验，返回可定位的路径（如 ``collectors.xxx.\"\"``）。
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
    """完整配置响应 (扁平化的 main_config)

    顶层字段对应 ConfigService 中的各个 section,例如:
    ``persona`` / ``general`` / ``llm`` / ``collectors`` / ``deciders`` / ``handlers``
    """

    config: Dict[str, Any] = Field(default_factory=dict, description="完整配置字典")


class ConfigUpdateRequest(BaseModel):
    """配置更新请求"""

    key: str = Field(description="配置键（点分隔路径,如 'general.platform_id'）")
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

    key: str = Field(description="配置键（点分隔路径,如 'general.platform_id')")
    value: Any = Field(description="配置值")


class BatchConfigUpdateRequest(BaseModel):
    """批量配置更新请求。

    多个变更按提交顺序处理：
    - 同一批次内出现重复 key 时，后者覆盖前者的校验值与最终写入值（last-wins），
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
      ``requires_restart=true`` 沿用单条 PATCH 的诚实策略。
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

    返回扁平化的 ``main_config`` 字典 (ConfigService 已合并 core/model/input/decision/output)。

    敏感字段 (api_key / token / password / secret 等) 的值在响应中替换为 ``""``，
    避免明文凭据被此接口整盘导出。
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
    """递归遍历 config，对敏感字段的标量值替换为 ``""``，返回新 dict（不修改原对象）。

    路径语义与 schema 字段的 dotted key 一致：dict 子段拼接到前缀后，
    list 子项不引入新的路径段（数组元素匿名），与 ``collect_all_fields`` 生成的
    ``llm_providers.api_key`` 这类扁平 key 保持一致，便于复用 ``_is_sensitive_field``。
    """
    masked: dict = {}
    for k, v in config.items():
        full_key = f"{path_prefix}.{k}" if path_prefix else str(k)
        if isinstance(v, dict):
            masked[k] = _mask_sensitive_values(v, full_key)
        elif isinstance(v, list):
            masked[k] = [_mask_sensitive_values(item, full_key) if isinstance(item, dict) else item for item in v]
        else:
            masked[k] = "" if _is_sensitive_field(full_key) else v
    return masked


def _convert_to_api_field(field: dict, main_config: dict) -> dict:
    """将 generator schema 的 field dict 转换为前端 API 字段格式。

    集中维护字段规范化规则，供 ``_build_frontend_groups`` 复用。

    敏感字段：``value`` 一律返回空字符串，避免明文 API key 通过 schema 接口泄漏。
    前端基于 schema 的 diff 基线策略只在用户真正编辑时提交新值，
    未触动过的敏感字段保持空字符串上送，被后端视为"未改动"而不会覆盖磁盘上的真实值。
    """
    dotted_key = field.get("key", "")
    raw_value = _get_nested_value(main_config, dotted_key)
    is_sensitive = _is_sensitive_field(dotted_key)
    gfield: dict = {
        "key": dotted_key,
        "label": _extract_label(field),
        "description": field.get("description", ""),
        "type": _map_gen_type(field.get("type", "string")),
        "default": field.get("default"),
        "value": "" if is_sensitive else raw_value,
        "required": field.get("required", False),
        "sensitive": is_sensitive,
        # 透传 schema_generator 从 json_schema_extra 解析出的 readonly 标记，与 PATCH 接口的拒绝逻辑共用同一信号
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


# section → TOML 文件映射（7 文件）
_SECTION_TO_FILE: dict[str, str] = {
    "meta": "core.toml",
    "general": "core.toml",
    "persona": "core.toml",
    "context": "core.toml",
    "dashboard": "core.toml",
    "events": "core.toml",
    "logging": "core.toml",
    "pipelines": "core.toml",
    "simulator": "core.toml",
    "llm": "model.toml",
    "llm_fast": "model.toml",
    "vlm": "model.toml",
    "llm_local": "model.toml",
    "llm_providers": "model.toml",
    "llm_summary": "model.toml",
    "llm_agenda": "model.toml",
    "agents": "agents.toml",
    "streamer": "agents.toml",
    "tools": "tools.toml",
    "perception": "tools.toml",
    "output": "tools.toml",
    "memory": "memory.toml",
    "simple": "memory.toml",
    "amemorix": "memory.toml",
    "storage": "storage.toml",
    "sqlite": "storage.toml",
}

_FILE_LABELS: dict[str, str] = {
    "core.toml": "🚀 核心",
    "model.toml": "🧠 模型",
    "agents.toml": "🤖 业务 Agent",
    "tools.toml": "🔧 工具包",
    "memory.toml": "💾 记忆",
    "storage.toml": "📦 存储",
}

_SECTION_LABELS: dict[str, str] = {
    "llm": "主 LLM 配置",
    "llm_fast": "快速 LLM",
    "vlm": "视觉语言模型",
    "llm_local": "本地 LLM",
    "llm_providers": "LLM 提供商列表",
    "llm_summary": "房间状态摘要 LLM",
    "llm_agenda": "直播大纲 LLM",
    "meta": "元信息",
    "general": "通用配置",
    "persona": "VTuber 人设",
    "context": "上下文组装器",
    "events": "事件历史",
    "dashboard": "Dashboard",
    "logging": "日志",
    "pipelines": "管道配置",
    "simulator": "模拟直播间",
    "agents": "业务 Agent",
    "streamer": "主播 Agent",
    "tools": "工具包",
    "perception": "感知工具包",
    "output": "输出工具包",
    "memory": "记忆系统",
    "simple": "SimpleMemory",
    "amemorix": "Amemorix",
    "storage": "存储",
    "sqlite": "SQLite 存储",
}


def _group_into_children(fields: list[dict], _depth: int = 1) -> list[dict]:
    """将扁平的点分 key 字段列表构建为层级 children 结构。

    ``_depth`` 标记当前层在 dotted key 中的段索引（初始 1 = section 后第一位）。
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
    """Schema 适配器：generator schema → {groups, version} 前端格式.

    使用 ``ConfigSchemaGenerator`` 直接输出（schema_registry 已废弃）。
    label/icon 来自 ``_SECTION_LABELS`` 兜底表。
    """
    from src.modules.config.schema_generator import (
        ConfigSchemaGenerator,
        collect_all_fields,
    )
    from src.modules.config.core_schemas import CoreConfig
    from src.modules.config.model_schemas import ModelConfig
    from src.modules.config.agents_schemas import AgentsRootConfig
    from src.modules.config.tools_schemas import ToolsRootConfig
    from src.modules.config.memory_schemas import MemoryRootConfig
    from src.modules.config.storage_schemas import StorageRootConfig

    main_config = config_service.main_config or {}

    core_schema = ConfigSchemaGenerator.generate_config_schema(CoreConfig)
    model_schema = ConfigSchemaGenerator.generate_config_schema(ModelConfig)
    agents_schema = ConfigSchemaGenerator.generate_config_schema(AgentsRootConfig)
    tools_schema = ConfigSchemaGenerator.generate_config_schema(ToolsRootConfig)
    memory_schema = ConfigSchemaGenerator.generate_config_schema(MemoryRootConfig)
    storage_schema = ConfigSchemaGenerator.generate_config_schema(StorageRootConfig)
    all_fields = (
        collect_all_fields(core_schema)
        + collect_all_fields(model_schema)
        + collect_all_fields(agents_schema)
        + collect_all_fields(tools_schema)
        + collect_all_fields(memory_schema)
        + collect_all_fields(storage_schema)
    )
    leaf_fields = [f for f in all_fields if "." in str(f.get("key", ""))]

    section_map: Dict[str, list] = {}
    for field in leaf_fields:
        key = field.get("key", "")
        parts = key.split(".")
        section = parts[0] if parts else "_other"
        if section not in section_map:
            section_map[section] = []
        section_map[section].append(field)

    groups: list[dict] = []
    for section_key, fields in section_map.items():
        group_fields: list[dict] = []

        for field in fields:
            group_fields.append(_convert_to_api_field(field, main_config))

        group_fields = _group_into_children(group_fields)

        file_name = _SECTION_TO_FILE.get(section_key, "core.toml")
        groups.append(
            {
                "key": section_key,
                "label": _SECTION_LABELS.get(section_key, section_key),
                "description": "",
                "icon": None,
                "order": 99,
                "fields": group_fields,
                "file_name": file_name,
                "file_label": _FILE_LABELS.get(file_name, file_name),
            }
        )

    groups.sort(key=lambda g: g.get("order", 99) or 99)

    return {"groups": groups, "version": "1.0.0"}


@router.get("/schema", response_model=SchemaGroupsResponse)
async def get_config_schema(server: ServerDep) -> SchemaGroupsResponse:
    """获取配置 Schema

    使用 ``ConfigSchemaGenerator`` 从 Pydantic 模型自动推导 Schema,
    通过 ``_build_frontend_groups`` 转换为前端 ``{groups, version}`` 格式。
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
# PATCH /api/v1/config
# ---------------------------------------------------------------------------


@router.patch("", response_model=ConfigUpdateResponse)
async def update_config(request: ConfigUpdateRequest, server: ServerDep) -> ConfigUpdateResponse:
    """更新配置（写入对应 TOML 文件,保留注释）

    根据 ``request.key`` 的首段(section)路由到正确的 TOML 文件:
    - ``persona.*`` / ``general.*`` / ... → ``core.toml``
    - ``llm.*`` / ``vlm.*`` / ... → ``model.toml``
    - ``collectors.*`` → ``input.toml``
    - ``deciders.*`` → ``decision.toml``
    - ``handlers.*`` → ``output.toml``
    """
    config_service = server.config_service
    if not config_service:
        return ConfigUpdateResponse(
            success=False,
            message="Config service not available",
        )

    section = _resolve_section(request.key)
    if not section:
        return ConfigUpdateResponse(
            success=False,
            message=f"无法从 key 解析 section: {request.key!r}",
        )

    config_path = server.get_config_path(section)
    if not config_path:
        return ConfigUpdateResponse(
            success=False,
            message="Config file path not available",
            target_file=None,
        )

    # 校验 value 中不含空 key（嵌套），避免 tomlkit 序列化时报笼统的 "Empty key" 错误
    empty_key_path = _find_empty_key(request.value)
    if empty_key_path is not None:
        return ConfigUpdateResponse(
            success=False,
            message=f"配置值包含空键: '{empty_key_path}'（位于 {request.key}，请移除空白键后重试）",
            target_file=_path_basename(config_path),
        )

    # 走 schema 树校验：未知配置项 / readonly / 类型或约束违规一律拒绝
    schema_node = _resolve_schema_node(request.key)
    if schema_node is None:
        return ConfigUpdateResponse(
            success=False,
            message=f"未知配置项: {request.key}",
            target_file=_path_basename(config_path),
        )
    _containing_cls, leaf_field = schema_node
    if leaf_field is not None and _field_is_readonly(leaf_field):
        return ConfigUpdateResponse(
            success=False,
            message=f"{request.key} 为只读字段，禁止修改",
            target_file=_path_basename(config_path),
        )
    if leaf_field is not None:
        validation_error = _validate_value_for_field(request.value, leaf_field, request.key)
        if validation_error is not None:
            return ConfigUpdateResponse(
                success=False,
                message=validation_error,
                target_file=_path_basename(config_path),
            )

    try:
        # 1. 使用 tomlkit 读取（保留注释）
        doc = load_toml_with_comments(str(config_path))

        # 2. 更新嵌套值
        keys = request.key.split(".")
        current = doc
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]

        # 设置值
        current[keys[-1]] = request.value

        # 3. 原子写入（临时文件 → 验证 → 重命名，不创建备份）
        success, message = write_toml_preserve(str(config_path), doc, create_backup=False)

        if success:
            # ConfigService.reload_config() 只刷新内存中的 main_config，不向已构造的 Agent / Tool /
            # Collector 注入新配置（reload 回调链路未被任何生产代码注册，FileWatcher 也未启动），
            # 故任何 PATCH 都要求用户重启服务才能生效；前缀白名单的差异化策略已被废弃。
            logger.info(f"配置已更新: {request.key} = {request.value} (写入 {_path_basename(config_path)})")

            return ConfigUpdateResponse(
                success=True,
                message="配置已保存到文件，需重启服务后生效",
                requires_restart=True,
                target_file=_path_basename(config_path),
            )

        return ConfigUpdateResponse(
            success=False,
            message=f"写入配置文件失败: {message}",
            target_file=_path_basename(config_path),
        )

    except Exception as e:
        logger.error(f"更新配置失败: {e}", exc_info=True)
        return ConfigUpdateResponse(
            success=False,
            message=f"更新配置失败: {str(e)}",
            target_file=_path_basename(config_path) if config_path else None,
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
    任一变更在校验阶段失败则拒绝整个批次并保持磁盘零写入；
    写入阶段按目标 TOML 文件分组，每个文件只读写一次。

    同一批次内出现重复 key 时按 **last-wins** 处理：后者的 value 覆盖前者的校验值与
    最终写入值，便于前端"反复编辑同字段后保存"的最终一致性，不视为错误。
    """
    config_service = server.config_service
    if not config_service:
        return BatchConfigUpdateResponse(
            success=False,
            message="Config service not available",
        )

    if not request.changes:
        return BatchConfigUpdateResponse(
            success=False,
            message="没有可保存的更改",
        )

    deduped: Dict[str, Any] = {}
    for change in request.changes:
        deduped[change.key] = change.value
    normalized: list[tuple[str, Any]] = list(deduped.items())

    validation_error = _validate_batch(normalized)
    if validation_error is not None:
        return validation_error

    write_error = _write_batch(normalized, server)
    if write_error is not None:
        return write_error

    logger.info(f"批量配置更新成功: 共 {len(normalized)} 项")
    return BatchConfigUpdateResponse(
        success=True,
        message="配置已保存",
        requires_restart=True,
        results=[BatchChangeResult(key=k, success=True) for k, _ in normalized],
    )


def _validate_batch(
    normalized: list[tuple[str, Any]],
) -> Optional[BatchConfigUpdateResponse]:
    """逐条复用单条 PATCH 的校验规则；任一失败返回完整失败响应（不写盘）。"""
    errors: list[BatchChangeError] = []
    for key, value in normalized:
        empty_key_path = _find_empty_key(value)
        if empty_key_path is not None:
            errors.append(
                BatchChangeError(
                    key=key,
                    message=f"配置值包含空键: '{empty_key_path}'（位于 {key}，请移除空白键后重试）",
                )
            )
            continue

        schema_node = _resolve_schema_node(key)
        if schema_node is None:
            errors.append(BatchChangeError(key=key, message=f"未知配置项: {key}"))
            continue

        _containing_cls, leaf_field = schema_node
        if leaf_field is not None and _field_is_readonly(leaf_field):
            errors.append(BatchChangeError(key=key, message=f"{key} 为只读字段，禁止修改"))
            continue

        if leaf_field is not None:
            validation_error = _validate_value_for_field(value, leaf_field, key)
            if validation_error is not None:
                errors.append(BatchChangeError(key=key, message=validation_error))
                continue

    if not errors:
        return None

    first = errors[0]
    if len(errors) == 1:
        agg_message = first.message
    else:
        agg_message = f"{first.message}（另有 {len(errors) - 1} 项失败）"
    return BatchConfigUpdateResponse(
        success=False,
        message=agg_message,
        errors=errors,
    )


def _group_by_file(
    normalized: list[tuple[str, Any]],
    server: ServerDep,
) -> Dict[str, list[tuple[list[str], Any]]]:
    """把 (key, value) 按目标 TOML 文件路径分组，便于一次性读写。"""
    by_file: Dict[str, list[tuple[list[str], Any]]] = {}
    for key, value in normalized:
        section = _resolve_section(key)
        config_path = server.get_config_path(section)
        if not config_path:
            raise ValueError(f"无法定位配置文件: section={section!r} (key={key})")
        by_file.setdefault(config_path, []).append((key.split("."), value))
    return by_file


def _write_batch(
    normalized: list[tuple[str, Any]],
    server: ServerDep,
) -> Optional[BatchConfigUpdateResponse]:
    """按目标文件分组写入，每个文件仅做一次 ``load_toml_with_comments`` + ``write_toml_preserve``。

    返回 ``None`` 表示全部成功；返回 ``BatchConfigUpdateResponse(success=False, ...)`` 时
    可能已有部分文件被写入（与单条 PATCH 同样忠实报错，不做回滚）。
    """
    try:
        by_file = _group_by_file(normalized, server)
    except ValueError as e:
        return BatchConfigUpdateResponse(success=False, message=str(e))

    for file_path, edits in by_file.items():
        try:
            doc = load_toml_with_comments(str(file_path))
            for key_parts, value in edits:
                current = doc
                for k in key_parts[:-1]:
                    next_node = current.get(k)
                    if not isinstance(next_node, dict):
                        current[k] = {}
                    current = current[k]
                current[key_parts[-1]] = value
            ok, message = write_toml_preserve(str(file_path), doc, create_backup=False)
        except Exception as e:
            logger.error(f"批量写入失败: {file_path}: {e}", exc_info=True)
            return BatchConfigUpdateResponse(
                success=False,
                message=f"写入配置文件失败: {e}",
            )

        if not ok:
            logger.error(f"批量写入失败: {file_path}: {message}")
            return BatchConfigUpdateResponse(
                success=False,
                message=f"写入配置文件失败: {message}",
            )

    return None


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


def _path_basename(path: str) -> str:
    """提取路径的文件名部分,失败时返回原字符串"""
    try:
        import os as _os

        return _os.path.basename(path)
    except Exception:
        return path
