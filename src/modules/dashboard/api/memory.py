"""观众画像管理端点（WebUI 观众画像页的数据接口）

``viewer_profiles`` 表的管理接口：印象列表 / **人工编辑纠正** / 删除 / 统计。
读写全部经 ``ViewerProfileRepo``，本模块不做 SQL。印象由主播 Agent 在收播时
生成（见 ``src/agents/streamer/memory/``），这里只提供查看与纠正。

- ``GET  /memory/stats``                               总量统计（常客数 / 最近更新）
- ``GET  /memory/profiles``                            画像列表（搜索 / 分页）
- ``PATCH /memory/profiles/{viewer_id}``               人工纠正印象文本
- ``DELETE /memory/profiles/{viewer_id}``              删除印象（删除后该观众回到无印象态）

画像以内部观众 ID 为键；列表行带观众的平台与当前昵称，供页面直接展示。

未注入仓储（极简启动 / 部分测试装配）时端点返回 503。
"""

from typing import TYPE_CHECKING, Annotated, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.modules.dashboard.dependencies import get_dashboard_server, server_component

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer
    from src.modules.storage.repos import ViewerProfileRepo

router = APIRouter()

# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


# 组件访问声明：观众画像仓储未装配（极简启动/测试）时统一 503
ViewerProfileRepoDep = Annotated[
    "ViewerProfileRepo", Depends(server_component("viewer_profile_repo", missing_detail="观众画像不可用"))
]


class MemoryStatsResponse(BaseModel):
    """总量统计：常客数 / 最近一次印象更新时刻（空库为 0）。"""

    profile_count: int
    latest_updated_ms: int


class ViewerProfileItem(BaseModel):
    """一位观众的印象（``viewer_profiles`` 行内容 + 观众的平台与当前昵称）。"""

    viewer_id: int
    platform: str
    user_name: str
    profile_text: str
    last_compressed_at_ms: int = Field(description="已整理到的时间点（该时刻前的弹幕与付费记录已融进印象）")
    updated_at_ms: int


class ViewerProfileListResponse(BaseModel):
    """画像列表响应：``total`` 为命中搜索条件的全量行数（分页器用）。"""

    total: int
    items: List[ViewerProfileItem]


class ViewerProfileUpdateRequest(BaseModel):
    """印象纠正请求：人工编辑后的完整印象文本。"""

    profile_text: str = Field(min_length=1, description="印象文本（空白串拒绝）")


class MutationResponse(BaseModel):
    """更新 / 删除的通用响应（``success=False`` 多为目标不存在）。"""

    success: bool


@router.get("/stats", response_model=MemoryStatsResponse)
async def get_memory_stats(repo: ViewerProfileRepoDep) -> MemoryStatsResponse:
    """画像总量统计（管理页头部概览）。"""
    total, rows = await repo.list_profiles(limit=1)
    latest = int(rows[0]["updated_at_ms"]) if rows else 0
    return MemoryStatsResponse(profile_count=total, latest_updated_ms=latest)


@router.get("/profiles", response_model=ViewerProfileListResponse)
async def list_viewer_profiles(
    repo: ViewerProfileRepoDep,
    search: str = Query(default="", max_length=200, description="子串搜索印象文本 / 观众昵称"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ViewerProfileListResponse:
    """画像列表（搜索 / 分页）。"""
    total, rows = await repo.list_profiles(search=search, limit=limit, offset=offset)
    return ViewerProfileListResponse(
        total=total,
        items=[
            ViewerProfileItem(
                viewer_id=int(row["viewer_id"]),
                platform=str(row["platform"]),
                user_name=str(row["user_name"]),
                profile_text=str(row["profile_text"]),
                last_compressed_at_ms=int(row["last_compressed_at_ms"]),
                updated_at_ms=int(row["updated_at_ms"]),
            )
            for row in rows
        ],
    )


@router.patch("/profiles/{viewer_id}", response_model=MutationResponse)
async def update_viewer_profile(
    viewer_id: int,
    request: ViewerProfileUpdateRequest,
    repo: ViewerProfileRepoDep,
) -> MutationResponse:
    """人工纠正印象文本；空白文本 422，印象不存在 404。"""
    if not request.profile_text.strip():
        raise HTTPException(status_code=422, detail="profile_text 不能为空白")
    updated = await repo.update_profile_text(viewer_id=viewer_id, profile_text=request.profile_text)
    if not updated:
        raise HTTPException(status_code=404, detail=f"印象不存在: 观众 #{viewer_id}")
    return MutationResponse(success=True)


@router.delete("/profiles/{viewer_id}", response_model=MutationResponse)
async def delete_viewer_profile(viewer_id: int, repo: ViewerProfileRepoDep) -> MutationResponse:
    """删除印象；不存在时 404。"""
    deleted = await repo.delete_profile(viewer_id=viewer_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"印象不存在: 观众 #{viewer_id}")
    return MutationResponse(success=True)
