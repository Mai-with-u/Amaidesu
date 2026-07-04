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

from typing import TYPE_CHECKING, Annotated, Any, Dict, Optional
import asyncio
import os
import subprocess
import sys

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.modules.config.toml_utils import (
    load_toml_with_comments,
    write_toml_preserve,
)
from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("ConfigAPI")

# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


# 需要重启服务的配置键前缀
RESTART_REQUIRED_PREFIXES = [
    "llm.",
    "llm_fast.",
    "vlm.",
    "llm_local.",
    "maicore.",
    "dashboard.",
    "logging.",
    "mcp.",
]


def _check_requires_restart(key: str) -> bool:
    """检查配置更改是否需要重启"""
    for prefix in RESTART_REQUIRED_PREFIXES:
        if key.startswith(prefix):
            return True
    return False


def _resolve_section(key: str) -> str:
    """从点分 key 解析顶层 section (例如 'persona.bot_name' → 'persona')"""
    if not key:
        return ""
    return key.split(".", 1)[0]


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


# ---------------------------------------------------------------------------
# GET /api/v1/config
# ---------------------------------------------------------------------------


@router.get("", response_model=ConfigResponse)
async def get_config(server: ServerDep) -> ConfigResponse:
    """获取当前配置

    返回扁平化的 ``main_config`` 字典 (ConfigService 已合并 core/model/input/decision/output)。
    """
    config_service = server.config_service
    if not config_service:
        logger.warning("Config service 不可用,返回空配置")
        return ConfigResponse()

    try:
        return ConfigResponse(config=dict(config_service.main_config or {}))
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


# 友好标签：英文下划线名 → 中文显示名
# 用于将 schema generator 输出的 .title() 英文标签替换为更直观的中文标签
_FRIENDLY_LABELS: dict[str, str] = {
    # ---- Collectors（Input 阶段） ----
    "console_input": "控制台输入",
    "bili_danmaku": "B 站弹幕",
    "bili_danmaku_official": "B 站官方弹幕",
    "mainosaba": "主幕读取",
    "mock_danmaku": "模拟弹幕",
    "read_pingmu": "屏幕读取",
    "stt": "语音识别",
    # ---- Handlers（Output 阶段） ----
    "edge_tts": "Edge TTS",
    "gptsovits": "GPT-SoVITS",
    "omni_tts": "Omni TTS",
    "subtitle": "字幕",
    "sticker": "表情包",
    "vts": "VTubeStudio",
    "warudo": "Warudo",
    "vrchat": "VRChat",
    "obs_control": "OBS 控制",
    "debug_console": "调试控制台",
    "remote_stream": "远程流",
    # ---- Deciders（Decision 阶段） ----
    "llm": "LLM 决策",
    "maibot": "MaiBot 决策",
    "amaidesu": "Amaidesu 决策",
    "command": "命令决策",
    "replay": "回放决策",
}


def _convert_to_api_field(field: dict, main_config: dict) -> dict:
    """将 generator schema 的 field dict 转换为前端 API 字段格式。

    集中维护转换逻辑，使 ``_build_frontend_groups`` 与
    ``_expand_sub_config_fields`` 复用同一份字段规范化规则。
    """
    dotted_key = field.get("key", "")
    gfield: dict = {
        "key": dotted_key,
        "label": _extract_label(field),
        "description": field.get("description", ""),
        "type": _map_gen_type(field.get("type", "string")),
        "default": field.get("default"),
        "value": _get_nested_value(main_config, dotted_key),
        "required": field.get("required", False),
        "sensitive": _is_sensitive_field(dotted_key),
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


def _expand_sub_config_fields(group_fields: list[dict], main_config: dict) -> list[dict]:
    """为字段 leaf 名匹配 ``CONFIG_SCHEMA_REGISTRY`` 的字段注入子字段。

    背景：
        ``InputCollectorsConfig`` 的 Collector 字段声明为 ``Optional[Any]``（规避循环 import），
        因此 ``ConfigSchemaGenerator`` 无法递归出 ``collectors.console_input.user_id`` 这类叶子字段。
        这些字段在 API 层以 ``type="string"`` 的形式返回，前端 FieldRenderer 会把字典值渲染成空卡片。

    修复：
        利用 ``@collector/@handler/@decider`` 装饰器填充的 ``CONFIG_SCHEMA_REGISTRY``，
        对 leaf 名匹配注册项的字段手动展开其 schema，作为新条目追加到扁平列表中。
        后续 ``_group_into_children`` 会按 dotted key 把它们归入对应父字段下，形成 sub-card。

    幂等性：
        - 已有 ``children`` 的字段（OutputHandlersConfig/DecisionDecidersConfig 已经正确展开）跳过
        - leaf 名不在 registry 中的字段跳过
        - 输入 / 输出列表均不修改原对象，返回新列表
    """
    # 延迟 import：避免 config 模块加载时拉起 collector 模块（潜在循环依赖）
    from src.modules.config.schemas import CONFIG_SCHEMA_REGISTRY
    from src.modules.config.schema_generator import (
        ConfigSchemaGenerator,
        collect_all_fields,
    )

    expanded: list[dict] = []
    for field in group_fields:
        # --- 跳过条件（按优先级） ---
        # 1. 已有 children → 已经展开过
        if field.get("children"):
            expanded.append(field)
            continue

        parts = field.get("key", "").split(".")
        # 2. 单层字段（如 ``enabled``）无需展开
        if len(parts) < 2:
            expanded.append(field)
            continue

        # 3. 非 ``type="string"`` → 已经是 schema generator 正确展开的 object 类型
        #    （handlers/deciders 的 Optional[ConfigSchema] 被正确映射为 object），无需二次展开。
        if field.get("type") != "string":
            expanded.append(field)
            continue

        # 4. leaf 名不在 CONFIG_SCHEMA_REGISTRY 中 → 不是注册的组件
        leaf = parts[-1]
        schema_cls = CONFIG_SCHEMA_REGISTRY.get(leaf)
        if schema_cls is None:
            expanded.append(field)
            continue

        # --- 展开：不添加原始 string 字段，只注入子字段 ---
        # ``_group_into_children`` 会自动根据 dotted key 把子字段归入 sub-card。
        sub_schema = ConfigSchemaGenerator.generate_config_schema(schema_cls)
        sub_flat = collect_all_fields(sub_schema, prefix=field["key"])
        for sf in sub_flat:
            expanded.append(_convert_to_api_field(sf, main_config))

    return expanded


def _apply_friendly_labels(group_fields: list[dict]) -> list[dict]:
    """用 ``_FRIENDLY_LABELS`` 替换 ``_group_into_children`` 自动生成的英文 .title() 标签。

    递归处理 ``children`` 子树。匹配规则：字段 key 的最后一段在 ``_FRIENDLY_LABELS`` 中。
    """
    result: list[dict] = []
    for f in group_fields:
        key = f.get("key", "")
        parts = key.split(".")
        if len(parts) >= 2:
            leaf = parts[-1]
            friendly = _FRIENDLY_LABELS.get(leaf)
            if friendly:
                f = {**f, "label": friendly, "description": f"{friendly} 配置"}
        if f.get("children"):
            f = {**f, "children": _apply_friendly_labels(f["children"])}
        result.append(f)
    return result


# section → TOML 文件映射（与 server.py _SECTION_TO_CONFIG_FILE 同步）
_SECTION_TO_FILE: dict[str, str] = {
    "meta": "core.toml",
    "general": "core.toml",
    "persona": "core.toml",
    "maicore": "core.toml",
    "context": "core.toml",
    "dashboard": "core.toml",
    "logging": "core.toml",
    "pipelines": "core.toml",
    "mcp": "core.toml",
    "llm": "model.toml",
    "llm_fast": "model.toml",
    "vlm": "model.toml",
    "llm_local": "model.toml",
    "collectors": "input.toml",
    "deciders": "decision.toml",
    "handlers": "output.toml",
}

_FILE_LABELS: dict[str, str] = {
    "core.toml": "🚀 核心",
    "model.toml": "🧠 模型",
    "input.toml": "🎤 输入",
    "decision.toml": "🤔 决策",
    "output.toml": "📤 输出",
}

# section_key → 中文标签（无 registry 入口的 section 用此 fallback）
_SECTION_LABELS: dict[str, str] = {
    "llm": "主 LLM 配置",
    "llm_fast": "快速 LLM",
    "vlm": "视觉语言模型",
    "llm_local": "本地 LLM",
    "meta": "元信息",
    "pipelines": "管道配置",
    "mcp": "MCP 服务",
}


def _group_into_children(fields: list[dict], _depth: int = 1) -> list[dict]:
    """将扁平的点分 key 字段列表构建为层级 children 结构。

    ``_depth`` 标记当前层在 dotted key 中的段索引（初始 1 = section 后第一位）。
    递归时 ``_depth + 1``，不需要改动 key 本身。

    注意：同一 key 同时有扁平字段和嵌套子字段时（如 ``message_config`` 既是
    ``type="object"`` 又是更深层字段的父级），只保留容器（children），丢弃扁平字段，
    避免前端渲染出空卡片或 ``[object Object]``。
    """
    from collections import defaultdict

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
    """Schema 适配器：generator schema → {groups, version} 前端格式."""
    from src.modules.config.schema_generator import (
        ConfigSchemaGenerator,
        collect_all_fields,
    )
    from src.modules.config.schema_registry import get_schema_registry
    from src.modules.config.core_schemas import CoreConfig
    from src.modules.config.model_schemas import ModelConfig
    from src.modules.config.schemas.input_schemas import InputConfig
    from src.modules.config.schemas.output_schemas import OutputConfig
    from src.modules.config.schemas.decision_schemas import DecisionConfig

    main_config = config_service.main_config or {}
    registry = get_schema_registry()
    reg_groups = {g.key: g for g in registry.get_all_groups()}

    core_schema = ConfigSchemaGenerator.generate_config_schema(CoreConfig)
    model_schema = ConfigSchemaGenerator.generate_config_schema(ModelConfig)
    input_schema = ConfigSchemaGenerator.generate_config_schema(InputConfig)
    output_schema = ConfigSchemaGenerator.generate_config_schema(OutputConfig)
    decision_schema = ConfigSchemaGenerator.generate_config_schema(DecisionConfig)
    all_fields = (
        collect_all_fields(core_schema)
        + collect_all_fields(model_schema)
        + collect_all_fields(input_schema)
        + collect_all_fields(output_schema)
        + collect_all_fields(decision_schema)
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
        rg = reg_groups.get(section_key)
        group_fields: list[dict] = []

        for field in fields:
            group_fields.append(_convert_to_api_field(field, main_config))

        # 注入 Any 字段的子 schema（collectors.console_input 等无 children 字段）
        group_fields = _expand_sub_config_fields(group_fields, main_config)

        group_fields = _group_into_children(group_fields)

        # 用友好中文标签覆盖 _group_into_children 自动生成的 .title() 英文标签
        group_fields = _apply_friendly_labels(group_fields)

        file_name = _SECTION_TO_FILE.get(section_key, "core.toml")
        groups.append(
            {
                "key": section_key,
                "label": rg.label
                if rg and getattr(rg, "label", None)
                else _SECTION_LABELS.get(section_key, section_key),
                "description": getattr(rg, "description", "") if rg else "",
                "icon": getattr(rg, "icon", None) if rg else None,
                "order": getattr(rg, "order", 99) if rg else 99,
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
            requires_restart = _check_requires_restart(request.key)
            logger.info(f"配置已更新: {request.key} = {request.value} (写入 {_path_basename(config_path)})")

            return ConfigUpdateResponse(
                success=True,
                message="配置已保存到文件",
                requires_restart=requires_restart,
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
