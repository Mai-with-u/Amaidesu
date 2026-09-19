"""流程单库（Rundown Library）CRUD API

承载 Dashboard 编排页的流程单库管理端点：

- ``GET    /api/v1/rundowns``            — 库列表（完整定义）+ 当前配置指向
- ``GET    /api/v1/rundowns/template``   — 内置默认流程单（新建预填模板）
- ``POST   /api/v1/rundowns``            — upsert（新建与保存共用）
- ``DELETE /api/v1/rundowns/{id}``       — 删除
- ``POST   /api/v1/rundowns/{id}/duplicate`` — 复制
- ``POST   /api/v1/rundowns/{id}/activate``  — 设为当前（写配置）

数据来源与职责切分
------------------
- 存储读写 → ``RundownRepo``（组合根注入 ``DashboardServer.rundown_repo``）；
- 运行态写穿 → ``StreamerAgent.apply_rundown_definition`` 公开门面（保存的
  流程单正是直播运行中的那份时，经它同步运行态、进度按环节 id 对齐）；
- 配置指向 → ``config_service.main_config["agents"]["streamer"]["rundown_id"]``
  （activate 经 config PATCH 统一管线落盘）。

错误约定：不抛 HTTPException，一律 ``{success, message}`` 包络；定义完整性
校验（环节 id 唯一、时长下界等）转 success=false + 原因。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Dict, Optional, Protocol, cast

from fastapi import APIRouter, Depends

from src.modules.dashboard.api.common import resolve_streamer_agent
from src.modules.dashboard.api.config import ConfigUpdateRequest, update_config
from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.rundown import (
    RundownDefinition,
    RundownListResponse,
    RundownMutateResponse,
    RundownSegmentView,
    RundownTemplateResponse,
)
from src.modules.storage.models.rundown import DEFAULT_RUNDOWN, Rundown
from src.modules.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer


router = APIRouter()

# 类型别名：FastAPI 依赖注入的 DashboardServer
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


class _RundownApplyCallable(Protocol):
    """StreamerAgent.apply_rundown_definition 的鸭子接口（由 facade 实现）。

    Dashboard 只依赖这一最小契约，不 import Agent 本体。
    """

    async def __call__(
        self,
        definition: Dict[str, Any],
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]: ...


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _get_repo(server: "DashboardServer") -> Optional[Any]:
    """取组合根注入的 RundownRepo（未注入返回 None，端点降级）。"""
    return getattr(server, "rundown_repo", None)


def _read_config_rundown_id(server: "DashboardServer") -> str:
    """读 agents.streamer.rundown_id 配置（当前流程单指向；缺字段用空串）。"""
    main_config = server.config_service.main_config if server.config_service else {}
    streamer_cfg = ((main_config or {}).get("agents") or {}).get("streamer") or {}
    return str(streamer_cfg.get("rundown_id", "") or "")


def _definition_from_rundown(rundown: Any) -> RundownDefinition:
    """把存储层返回的 ``Rundown`` 模型转成 Dashboard 定义视图。"""
    return RundownDefinition(
        rundown_id=rundown.rundown_id,
        title=rundown.title,
        segments=[RundownSegmentView(**seg.model_dump()) for seg in rundown.segments],
    )


def _validate_definition(payload: RundownDefinition) -> Optional[str]:
    """用领域模型校验定义完整性，返回错误原因（None = 通过）。"""
    try:
        Rundown.model_validate(payload.model_dump())
    except Exception as exc:
        return str(exc)
    return None


async def _apply_to_runtime(
    server: "DashboardServer",
    definition: RundownDefinition,
) -> Optional[str]:
    """运行态写穿：保存的是直播运行中的那份流程单时同步 Agent 运行态。

    返回提示信息（None = 无需写穿或写穿成功）；Agent 缺席视为无需写穿。
    """
    agent = resolve_streamer_agent(server)
    if agent is None:
        return None
    apply_raw = getattr(agent, "apply_rundown_definition", None)
    if not callable(apply_raw):
        return None
    apply = cast(_RundownApplyCallable, apply_raw)
    _, message, _ = await apply(definition.model_dump())
    return message


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.get("", response_model=RundownListResponse)
async def list_rundowns(server: ServerDep) -> RundownListResponse:
    """流程单库列表（完整定义；每单 3-10 环节，全量返回无分页负担）。"""
    repo = _get_repo(server)
    if repo is None:
        return RundownListResponse(success=False, message="流程单存储未就绪")
    try:
        rows = await repo.list_rundowns()
    except Exception as exc:
        return RundownListResponse(success=False, message=f"读取流程单库失败: {exc}")
    return RundownListResponse(
        success=True,
        rundowns=[_definition_from_rundown(row) for row in rows],
        current_id=_read_config_rundown_id(server),
    )


@router.get("/template", response_model=RundownTemplateResponse)
async def get_rundown_template(server: ServerDep) -> RundownTemplateResponse:
    """内置默认流程单（新建预填模板；虚拟存在不写库）。"""
    del server  # 配置无关的静态模板，占位参数保持依赖注入形态一致
    return RundownTemplateResponse(success=True, definition=_definition_from_rundown(DEFAULT_RUNDOWN))


@router.post("", response_model=RundownMutateResponse)
async def upsert_rundown(definition: RundownDefinition, server: ServerDep) -> RundownMutateResponse:
    """新建或整体保存一份流程单（repo 落盘；运行中的同 id 单即时写穿）。"""
    repo = _get_repo(server)
    if repo is None:
        return RundownMutateResponse(success=False, message="流程单存储未就绪")

    error = _validate_definition(definition)
    if error is not None:
        return RundownMutateResponse(
            success=False, message=f"流程单定义不合法: {error}", rundown_id=definition.rundown_id
        )

    try:
        await repo.upsert_rundown(Rundown.model_validate(definition.model_dump()))
    except Exception as exc:
        return RundownMutateResponse(success=False, message=f"保存流程单失败: {exc}", rundown_id=definition.rundown_id)

    runtime_message = await _apply_to_runtime(server, definition)
    message = "流程单已保存" if runtime_message is None else f"流程单已保存（{runtime_message}）"
    return RundownMutateResponse(success=True, message=message, rundown_id=definition.rundown_id)


@router.delete("/{rundown_id}", response_model=RundownMutateResponse)
async def delete_rundown(rundown_id: str, server: ServerDep) -> RundownMutateResponse:
    """删除流程单（运行态不动：Agent 内存副本继续用，重启后按回退语义走默认单）。"""
    repo = _get_repo(server)
    if repo is None:
        return RundownMutateResponse(success=False, message="流程单存储未就绪")
    try:
        deleted = await repo.delete_rundown(rundown_id)
    except Exception as exc:
        return RundownMutateResponse(success=False, message=f"删除流程单失败: {exc}", rundown_id=rundown_id)
    if not deleted:
        return RundownMutateResponse(success=False, message=f"流程单 '{rundown_id}' 不存在", rundown_id=rundown_id)

    message = "流程单已删除"
    if _read_config_rundown_id(server) == rundown_id:
        message += "（当前配置仍指向它；重启主播 Agent 后将回退内置默认流程单）"
    return RundownMutateResponse(success=True, message=message, rundown_id=rundown_id)


@router.post("/{rundown_id}/duplicate", response_model=RundownMutateResponse)
async def duplicate_rundown(rundown_id: str, server: ServerDep) -> RundownMutateResponse:
    """复制一份流程单（新 id 自动生成，标题追加"副本"）。"""
    repo = _get_repo(server)
    if repo is None:
        return RundownMutateResponse(success=False, message="流程单存储未就绪")
    try:
        source = await repo.get_rundown(rundown_id)
    except Exception as exc:
        return RundownMutateResponse(success=False, message=f"读取源流程单失败: {exc}", rundown_id=rundown_id)
    if source is None:
        return RundownMutateResponse(success=False, message=f"流程单 '{rundown_id}' 不存在", rundown_id=rundown_id)

    new_id = f"{rundown_id}_copy_{now_ms() % 100_000:05d}"
    source.rundown_id = new_id
    source.title = f"{source.title} 副本"
    try:
        await repo.upsert_rundown(source)
    except Exception as exc:
        return RundownMutateResponse(success=False, message=f"保存副本失败: {exc}", rundown_id=rundown_id)
    return RundownMutateResponse(success=True, message="副本已创建", rundown_id=new_id)


@router.post("/{rundown_id}/activate", response_model=RundownMutateResponse)
async def activate_rundown(rundown_id: str, server: ServerDep) -> RundownMutateResponse:
    """设为当前流程单（写配置落盘；重启主播 Agent 后生效，不热切运行中的单）。"""
    repo = _get_repo(server)
    if repo is None:
        return RundownMutateResponse(success=False, message="流程单存储未就绪")
    try:
        exists = await repo.get_rundown(rundown_id)
    except Exception as exc:
        return RundownMutateResponse(success=False, message=f"读取流程单失败: {exc}", rundown_id=rundown_id)
    if exists is None:
        return RundownMutateResponse(success=False, message=f"流程单 '{rundown_id}' 不存在", rundown_id=rundown_id)

    update = await update_config(
        ConfigUpdateRequest(key="agents.agents.streamer.rundown_id", value=rundown_id),
        server,
    )
    if not update.success:
        return RundownMutateResponse(success=False, message=f"配置写入失败: {update.message}", rundown_id=rundown_id)
    return RundownMutateResponse(
        success=True,
        message="已设为当前流程单（重启主播 Agent 后生效，当前直播不受影响）",
        rundown_id=rundown_id,
    )
