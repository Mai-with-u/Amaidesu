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
import os
import signal

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


class LegacyGroupsResponse(BaseModel):
    """对前端兼容的 groups 格式 (Shape Adapter)."""

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


def _build_legacy_groups(config_service) -> dict:
    """Shape Adapter: generator schema → {groups, version} 兼容前端."""
    from src.modules.config.schema_generator import (
        ConfigSchemaGenerator,
        collect_all_fields,
    )
    from src.modules.config.schema_registry import get_schema_registry
    from src.modules.config.core_schemas import CoreConfig
    from src.modules.config.model_schemas import ModelConfig

    main_config = config_service.main_config or {}
    registry = get_schema_registry()
    reg_groups = {g.key: g for g in registry.get_all_groups()}

    core_schema = ConfigSchemaGenerator.generate_config_schema(CoreConfig)
    model_schema = ConfigSchemaGenerator.generate_config_schema(ModelConfig)
    all_fields = collect_all_fields(core_schema) + collect_all_fields(model_schema)
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
            group_fields.append(gfield)

        groups.append(
            {
                "key": section_key,
                "label": rg.label if rg and getattr(rg, "label", None) else section_key,
                "description": getattr(rg, "description", "") if rg else "",
                "icon": getattr(rg, "icon", None) if rg else None,
                "order": getattr(rg, "order", 99) if rg else 99,
                "fields": group_fields,
            }
        )

    groups.sort(key=lambda g: g.get("order", 99) or 99)

    return {"groups": groups, "version": "1.0.0"}


@router.get("/schema", response_model=LegacyGroupsResponse)
async def get_config_schema(server: ServerDep) -> LegacyGroupsResponse:
    """获取配置 Schema (Shape Adapter 格式,对前端兼容)

    内部使用 ``ConfigSchemaGenerator`` 从 Pydantic 模型自动推导 Schema,
    但通过 ``_build_legacy_groups`` 适配器转换为前端 ``settings.ts`` 期望的
    ``{groups, version}`` 格式 (含 group 元数据、字段 Schema、当前值)。
    """
    config_service = server.config_service
    if not config_service:
        logger.warning("Config service 不可用,返回空 schema")
        return LegacyGroupsResponse()

    try:
        result = _build_legacy_groups(config_service)
        return LegacyGroupsResponse(**result)
    except Exception as e:
        logger.error(f"获取配置 Schema 失败: {e}", exc_info=True)
        return LegacyGroupsResponse()


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

        # 3. 使用原子写入（备份 → 临时文件 → 验证 → 重命名）
        success, message = write_toml_preserve(str(config_path), doc)

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
    """重启服务（通过优雅关闭，由外部进程管理器重启）"""
    try:
        logger.info("收到重启服务请求")

        # 发送重启信号
        # 这里我们只是标记需要重启，实际的重启由外部进程管理器处理
        # 例如 systemd、supervisor 或 Docker

        # 设置一个标志，让主循环检测并退出
        # 给自己发送 SIGTERM 信号，触发优雅关闭
        os.kill(os.getpid(), signal.SIGTERM)

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
