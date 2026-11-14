"""
Skills API（技能检视端点，纯只读）

- GET /api/v1/skills                -> 全量技能目录（本地 + 已登记远端）+ 远端登记状态
- GET /api/v1/skills/{name}/body    -> 技能正文预览：本地直接读，远端经游戏 Agent 向 MaiCraft 代读

数据源：
- 技能库：``src.modules.skills`` 全局单例（``get_skill_library``），本地技能来自
  ``src/**/skills/`` 约定扫描，远端玩法技能由游戏 Agent 运行期从 MaiCraft MCP
  ``skill://`` 资源登记（内存持有，不落库，Agent 未连接时为空）。
- 运行态：AgentManager 取 minecraft Agent 实例，鸭子型调公有快照方法
  （``remote_skill_status`` 登记态、``read_skill_body`` 远端正文代读，与 memo
  端点同一接缝模式）；Agent 不可观测时按 ``observable=false`` 降级。
- 远端正文代读走 Agent 的 ``minecraft_skill`` 同一条通道（模型读到什么预览
  显示什么），读取本身无状态副作用；Agent 未运行时 503 交代理由。
"""

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from src.modules.dashboard.dependencies import server_component
from src.modules.dashboard.schemas.skills import (
    SkillBodyResponse,
    SkillItem,
    SkillRemoteStatus,
    SkillsSnapshotResponse,
)
from src.modules.logging import get_logger
from src.modules.skills import Skill, SkillLibrary, RemoteSkill, get_skill_library

if TYPE_CHECKING:
    from src.modules.agents.manager import AgentManager

logger = get_logger("DashboardSkillsAPI")

router = APIRouter()

# 仓库 src/ 根：本地技能来源文件按它转成仓库相对路径再出接口
_SRC_ROOT = Path(__file__).resolve().parents[3]

AgentManagerOptionalDep = Annotated[
    Optional["AgentManager"], Depends(server_component("agent_manager", required=False))
]  # noqa: B008 - FastAPI 依赖注入模式


def _skill_library() -> SkillLibrary:
    """技能库单例取值点（FastAPI 依赖，测试用 dependency_overrides 换成受控库）。"""
    return get_skill_library()


SkillLibraryDep = Annotated[SkillLibrary, Depends(_skill_library)]  # noqa: B008 - FastAPI 依赖注入模式


def _relative_path(path: Path) -> str:
    """来源文件转仓库相对路径；不在 src/ 下时原样返回（异常布局不拦检视）。"""
    try:
        return path.resolve().relative_to(_SRC_ROOT).as_posix()
    except ValueError:
        return str(path)


def _local_item(skill: Skill) -> SkillItem:
    return SkillItem(
        name=skill.name,
        description=skill.metadata.description,
        category=skill.metadata.category,
        origin="local",
        agents=list(skill.metadata.agents),
        path=_relative_path(skill.path),
    )


def _remote_item(skill: RemoteSkill) -> SkillItem:
    return SkillItem(
        name=skill.name,
        description=skill.description,
        category=skill.category,
        origin=skill.origin,
        agents=list(skill.agents),
        uri=skill.uri,
    )


@router.get("/skills", response_model=SkillsSnapshotResponse, summary="技能检视快照（只读）")
async def get_skills_snapshot(
    library: SkillLibraryDep,
    manager: AgentManagerOptionalDep,
) -> SkillsSnapshotResponse:
    """全量技能目录与远端登记状态（不做受众与前提筛选，检视页要看到全部）。

    AgentManager 未注入或 minecraft Agent 未注册时 ``remote.observable=false``，
    远端条目以技能库现状为准（连接断开时 Agent 会自行撤掉远端条目）。
    """
    local_skills, remote_skills = library.snapshot()
    entries: List[SkillItem] = [_local_item(skill) for skill in local_skills]
    entries += [_remote_item(skill) for skill in remote_skills]
    entries.sort(key=lambda item: (item.category, item.name))

    remote_count = sum(1 for skill in remote_skills if skill.origin == "MaiCraft")
    agent_running: Optional[bool] = None
    remote_loaded: Optional[bool] = None
    agent = manager.get_agent_by_name("minecraft") if manager is not None else None
    if agent is not None:
        agent_running = manager.is_agent_alive("minecraft")
        status_fn = getattr(agent, "remote_skill_status", None)
        if callable(status_fn):
            remote_loaded = bool(status_fn().get("loaded"))

    return SkillsSnapshotResponse(
        entries=entries,
        remote=SkillRemoteStatus(
            observable=agent is not None,
            agent_running=agent_running,
            remote_loaded=remote_loaded,
            remote_count=remote_count,
        ),
    )


@router.get("/skills/{name}/body", response_model=SkillBodyResponse, summary="技能正文预览（远端经游戏 Agent 代读）")
async def get_skill_body(
    name: str,
    library: SkillLibraryDep,
    manager: AgentManagerOptionalDep,
) -> SkillBodyResponse:
    """本地技能直接取正文；远端技能经游戏 Agent 的 minecraft_skill 同通道向 MaiCraft 代读。

    代读前置是 Agent 在运行且装配了技能通道，不满足时 503 交代理由（不是 404：
    技能存在，是读取通道不在）；代读本身失败（服务端读不到等）同样 503 带原因。
    """
    local_skills, remote_skills = library.snapshot()
    skill = next((item for item in local_skills if item.name == name), None)
    if skill is not None:
        return SkillBodyResponse(name=skill.name, path=_relative_path(skill.path), content=skill.content)

    remote = next((item for item in remote_skills if item.name == name), None)
    if remote is not None:
        agent = manager.get_agent_by_name("minecraft") if manager is not None else None
        read_fn = getattr(agent, "read_skill_body", None)
        if not callable(read_fn):
            raise HTTPException(
                status_code=503,
                detail="远端技能正文要经游戏 Agent 向 MaiCraft 读取，当前 Agent 未运行或未装配技能通道",
            )
        result = await read_fn(name)
        if not result.get("success"):
            raise HTTPException(status_code=503, detail=str(result.get("error", f"读取技能 {name} 失败")))
        return SkillBodyResponse(name=name, path="", content=str(result.get("content", "")), source=remote.origin)

    raise HTTPException(status_code=404, detail=f"没有名为 {name} 的技能")
