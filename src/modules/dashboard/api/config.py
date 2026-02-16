"""
配置管理 API

提供配置的查询、Schema 获取和修改接口。
"""

from typing import Annotated, Any, Dict

from fastapi import APIRouter, Depends

from src.modules.config.schema_registry import get_schema_registry
from src.modules.config.toml_utils import (
    load_toml_with_comments,
    write_toml_preserve,
)
from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.config import (
    ConfigResponse,
    ConfigUpdateRequest,
    ConfigUpdateResponse,
)
from src.modules.dashboard.schemas.config_schema import (
    ConfigSchemaResponse,
    ConfigGroupSchema,
    ConfigFieldSchema,
)
from src.modules.dashboard.server import DashboardServer
from src.modules.logging import get_logger

router = APIRouter()
logger = get_logger("ConfigAPI")

# 类型别名，用于依赖注入
ServerDep = Annotated[DashboardServer, Depends(get_dashboard_server)]


# 需要重启服务的配置键前缀
RESTART_REQUIRED_PREFIXES = [
    "llm.",
    "llm_fast.",
    "vlm.",
    "llm_local.",
    "maicore.",
    "dashboard.",
    "http_server.",
    "logging.",
]


def _get_nested_value(data: Dict[str, Any], key: str) -> Any:
    """获取嵌套字典中的值"""
    keys = key.split(".")
    current = data
    for k in keys:
        if isinstance(current, dict) and k in current:
            current = current[k]
        else:
            return None
    return current


def _set_nested_value(data: Dict[str, Any], key: str, value: Any) -> None:
    """设置嵌套字典中的值"""
    keys = key.split(".")
    current = data
    for k in keys[:-1]:
        if k not in current:
            current[k] = {}
        current = current[k]
    current[keys[-1]] = value


def _check_requires_restart(key: str) -> bool:
    """检查配置更改是否需要重启"""
    for prefix in RESTART_REQUIRED_PREFIXES:
        if key.startswith(prefix):
            return True
    return False


@router.get("", response_model=ConfigResponse)
async def get_config(
    server: ServerDep,
) -> ConfigResponse:
    """获取当前配置"""
    config_service = server.config_service
    if not config_service:
        return ConfigResponse()

    try:
        # 尝试获取完整配置
        if hasattr(config_service, "get_all"):
            config = config_service.get_all()
        elif hasattr(config_service, "main_config"):
            config = config_service.main_config
        else:
            config = {}

        return ConfigResponse(
            general=config.get("general", {}),
            providers=config.get("providers", {}),
            pipelines=config.get("pipelines", {}),
            logging=config.get("logging", {}),
            context=config.get("context", {}),
            dashboard=config.get("dashboard", {}),
        )
    except Exception as e:
        logger.error(f"获取配置失败: {e}")
        return ConfigResponse()


@router.get("/schema", response_model=ConfigSchemaResponse)
async def get_config_schema(
    server: ServerDep,
) -> ConfigSchemaResponse:
    """获取配置 Schema（用于动态表单生成）"""
    config_service = server.config_service
    if not config_service:
        # 返回默认 Schema
        registry = get_schema_registry()
        schema_data = registry.get_schema_for_config({})
        return ConfigSchemaResponse(
            groups=[ConfigGroupSchema(**g) for g in schema_data["groups"]],
            version=schema_data["version"],
        )

    try:
        # 获取当前配置
        if hasattr(config_service, "main_config"):
            config = config_service.main_config
        else:
            config = {}

        # 生成带当前值的 Schema
        registry = get_schema_registry()
        schema_data = registry.get_schema_for_config(config)

        groups = []
        for group_data in schema_data["groups"]:
            fields = []
            for field_data in group_data["fields"]:
                fields.append(ConfigFieldSchema(**field_data))
            groups.append(
                ConfigGroupSchema(
                    key=group_data["key"],
                    label=group_data["label"],
                    description=group_data.get("description"),
                    icon=group_data.get("icon"),
                    fields=fields,
                    order=group_data.get("order", 0),
                )
            )

        return ConfigSchemaResponse(
            groups=groups,
            version=schema_data["version"],
        )
    except Exception as e:
        logger.error(f"获取配置 Schema 失败: {e}", exc_info=True)
        # 返回空的 Schema 响应
        return ConfigSchemaResponse(groups=[])


@router.patch("", response_model=ConfigUpdateResponse)
async def update_config(
    request: ConfigUpdateRequest,
    server: ServerDep,
) -> ConfigUpdateResponse:
    """更新配置（写入 TOML 文件，保留注释）"""
    config_service = server.config_service
    if not config_service:
        return ConfigUpdateResponse(
            success=False,
            message="Config service not available",
        )

    try:
        # 获取配置文件路径
        config_path = server.get_config_path()
        if not config_path:
            return ConfigUpdateResponse(
                success=False,
                message="Config file path not available",
            )

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
            # 4. 检查是否需要重启
            requires_restart = _check_requires_restart(request.key)
            logger.info(f"配置已更新: {request.key} = {request.value}")

            return ConfigUpdateResponse(
                success=True,
                message="配置已保存到文件",
                requires_restart=requires_restart,
            )
        else:
            return ConfigUpdateResponse(
                success=False,
                message=f"写入配置文件失败: {message}",
            )

    except Exception as e:
        logger.error(f"更新配置失败: {e}", exc_info=True)
        return ConfigUpdateResponse(
            success=False,
            message=f"更新配置失败: {str(e)}",
        )


@router.post("/restart", response_model=ConfigUpdateResponse)
async def restart_service(
    server: ServerDep,
) -> ConfigUpdateResponse:
    """重启服务（通过优雅关闭，由外部进程管理器重启）"""
    try:
        logger.info("收到重启服务请求")

        # 发送重启信号
        # 这里我们只是标记需要重启，实际的重启由外部进程管理器处理
        # 例如 systemd、supervisor 或 Docker

        # 设置一个标志，让主循环检测并退出
        import os
        import signal

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
