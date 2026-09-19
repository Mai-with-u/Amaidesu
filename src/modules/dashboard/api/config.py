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
业务装配（schema 适配、脱敏契约、写路径编排）在
``services.config_adapter``，本模块只保留路由声明与 HTTP 语义。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
import asyncio
import os
import subprocess
import sys

from fastapi import APIRouter, Depends, HTTPException

from src.modules.config.multi_file_loader import resolve_root_schema
from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.config import (
    BatchChangeError,
    BatchChangeResult,
    BatchConfigUpdateRequest,
    BatchConfigUpdateResponse,
    ConfigResponse,
    ConfigUpdateRequest,
    ConfigUpdateResponse,
    SchemaGroupsResponse,
)
from src.modules.dashboard.services.config_adapter import (
    _SCOPES,
    _build_frontend_groups,
    _mask_sensitive_values,
    _resolve_scope,
    apply_config_updates,
)
from src.modules.dashboard.utils.component_helper import config_dir as get_config_dir
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("ConfigAPI")

# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


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
        logger.exception(f"获取配置失败: {e}")
        return ConfigResponse()


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
        logger.exception(f"获取配置 Schema 失败: {e}")
        return SchemaGroupsResponse()


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

    outcome = await apply_config_updates(
        config_service,
        get_config_dir(server),
        [(request.key, request.value)],
    )
    if not outcome.success:
        raise HTTPException(status_code=422, detail=outcome.message)

    logger.info(f"配置已更新: {request.key} (写入 {outcome.target_file})")
    message = (
        "配置已保存，hot 段已即时生效" if not outcome.requires_restart else "配置已保存到文件，需重启服务后完全生效"
    )
    return ConfigUpdateResponse(
        success=True,
        message=message,
        requires_restart=outcome.requires_restart,
        target_file=outcome.target_file,
    )


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

    outcome = await apply_config_updates(
        config_service,
        get_config_dir(server),
        [(change.key, change.value) for change in request.changes],
    )
    if not outcome.success:
        if outcome.errors:
            return BatchConfigUpdateResponse(
                success=False,
                message=outcome.message,
                errors=[BatchChangeError(key=key, message=message) for key, message in outcome.errors],
            )
        # 前置校验通过后的失败来自统一管线的 Schema 校验，按 422 拒绝整批
        raise HTTPException(status_code=422, detail=outcome.message)

    logger.info(f"批量配置更新成功: 共 {len(outcome.applied)} 项")
    return BatchConfigUpdateResponse(
        success=True,
        message="配置已保存" + ("，hot 段已即时生效" if not outcome.requires_restart else "，需重启服务后完全生效"),
        requires_restart=outcome.requires_restart,
        results=[BatchChangeResult(key=key, success=True) for key, _ in outcome.applied],
    )


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
        logger.exception(f"重启服务失败: {e}")
        return ConfigUpdateResponse(
            success=False,
            message=f"重启服务失败: {str(e)}",
        )
