"""观众画像管理端点（WebUI 画像管理页数据面）

``viewer_profiles`` / ``viewer_facts`` 两张业务表的管理消费面：画像列表 /
查看 / **人工编辑纠正** / 删除，事实查看与单条删除。读写全部经
``SimpleMemory``（画像/事实读写服务），本模块不做 SQL。

- ``GET  /memory/stats``                      总量统计（画像数 / 事实数 / 最新更新）
- ``GET  /memory/profiles``                   画像列表（搜索 / 分页）
- ``PATCH /memory/profiles/{platform}/{user_id}`` 人工纠正画像文本
- ``DELETE /memory/profiles/{platform}/{user_id}`` 删除画像（删除后该观众回到无画像态）
- ``GET  /memory/facts``                      事实列表（按人查 / 关键词搜索）
- ``DELETE /memory/facts/{fact_id}``          删除单条事实（修正提取错误）

未注入记忆栈（极简启动 / 部分测试装配）时端点返回 503。
"""

from typing import TYPE_CHECKING, Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.modules.dashboard.dependencies import get_dashboard_server

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer
    from src.modules.memory.simple_memory import SimpleMemory

router = APIRouter()

# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


def _require_memory(server: "DashboardServer") -> "SimpleMemory":
    """取记忆栈实例，未装配时 503。"""
    memory = server.memory
    if memory is None:
        raise HTTPException(status_code=503, detail="记忆栈不可用")
    return memory


class MemoryStatsResponse(BaseModel):
    """总量统计：画像数 / 事实数 / 最新画像更新时刻（空库为 0）。"""

    profile_count: int
    fact_count: int
    latest_updated_ms: int


class ViewerProfileItem(BaseModel):
    """单份画像（``viewer_profiles`` 行投影）。"""

    platform: str
    user_id: str
    profile_text: str
    last_compressed_at_ms: int = Field(description="增量压缩水位（该时刻前的原料已摄入画像）")
    updated_at_ms: int


class ViewerProfileListResponse(BaseModel):
    """画像列表响应：``total`` 为命中搜索条件的全量行数（分页器用）。"""

    total: int
    items: List[ViewerProfileItem]


class ViewerProfileUpdateRequest(BaseModel):
    """画像纠正请求：人工编辑后的完整画像文本。"""

    profile_text: str = Field(min_length=1, description="画像文本（空白串拒绝）")


class MutationResponse(BaseModel):
    """更新 / 删除的通用响应（``success=False`` 多为目标不存在）。"""

    success: bool


class ViewerFactItem(BaseModel):
    """单条观众事实（``viewer_facts`` 行投影）。"""

    id: int
    platform: str
    user_id: str
    fact_text: str
    source_message_id: str
    created_at_ms: int


class ViewerFactListResponse(BaseModel):
    """事实列表响应（按人查模式附 ``total`` 为该观众事实总数）。"""

    total: int
    items: List[ViewerFactItem]


@router.get("/stats", response_model=MemoryStatsResponse)
async def get_memory_stats(server: ServerDep) -> MemoryStatsResponse:
    """画像库总量统计（管理页头部概览）。"""
    memory = _require_memory(server)
    profile_total, profiles = await memory.list_viewer_profiles(limit=1)
    fact_total = await memory.count_viewer_facts()
    latest = profiles[0].updated_at_ms if profiles else 0
    return MemoryStatsResponse(
        profile_count=profile_total,
        fact_count=fact_total,
        latest_updated_ms=latest,
    )


@router.get("/profiles", response_model=ViewerProfileListResponse)
async def list_viewer_profiles(
    server: ServerDep,
    search: str = Query(default="", max_length=200, description="子串搜索画像文本 / user_id"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ViewerProfileListResponse:
    """画像列表（搜索 / 分页）。"""
    memory = _require_memory(server)
    total, profiles = await memory.list_viewer_profiles(
        search=search,
        limit=limit,
        offset=offset,
    )
    return ViewerProfileListResponse(
        total=total,
        items=[
            {
                "platform": p.platform,
                "user_id": p.user_id,
                "profile_text": p.profile_text,
                "last_compressed_at_ms": p.last_compressed_at_ms,
                "updated_at_ms": p.updated_at_ms,
            }
            for p in profiles
        ],
    )


@router.patch("/profiles/{platform}/{user_id}", response_model=MutationResponse)
async def update_viewer_profile(
    platform: str,
    user_id: str,
    request: ViewerProfileUpdateRequest,
    server: ServerDep,
) -> MutationResponse:
    """人工纠正画像文本；空白文本 422，画像不存在 404。"""
    memory = _require_memory(server)
    if not request.profile_text.strip():
        raise HTTPException(status_code=422, detail="profile_text 不能为空白")
    updated = await memory.update_viewer_profile_text(
        platform=platform,
        user_id=user_id,
        profile_text=request.profile_text,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"画像不存在: {platform}/{user_id}")
    return MutationResponse(success=True)


@router.delete("/profiles/{platform}/{user_id}", response_model=MutationResponse)
async def delete_viewer_profile(platform: str, user_id: str, server: ServerDep) -> MutationResponse:
    """删除画像；画像不存在时 404。"""
    memory = _require_memory(server)
    deleted = await memory.delete_viewer_profile(platform=platform, user_id=user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"画像不存在: {platform}/{user_id}")
    return MutationResponse(success=True)


@router.get("/facts", response_model=ViewerFactListResponse)
async def list_viewer_facts(
    server: ServerDep,
    platform: Optional[str] = Query(default=None, description="平台标识（与 user_id 组成身份键按人查）"),
    user_id: Optional[str] = Query(default=None, description="平台用户 ID"),
    search: str = Query(default="", max_length=200, description="关键词搜索事实文本"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ViewerFactListResponse:
    """事实列表：按人查（platform+user_id）或关键词搜索（二选一，按人优先）。

    提取错误的事实可经 DELETE 单条清理；画像不受影响（下次压缩以剩余
    事实为原料）。
    """
    memory = _require_memory(server)
    if platform and user_id:
        facts = await memory.list_viewer_facts(platform=platform, user_id=user_id, limit=limit + offset)
        window = facts[offset : offset + limit]
        return ViewerFactListResponse(total=len(facts), items=[_fact_item(f) for f in window])

    keyword = (search or "").strip()
    if not keyword:
        raise HTTPException(status_code=422, detail="请提供 platform+user_id（按人查）或 search（关键词）")
    facts = await memory.search_viewer_facts(query=keyword, top_k=min(limit, 20))
    return ViewerFactListResponse(total=len(facts), items=[_fact_item(f) for f in facts])


def _fact_item(fact: object) -> dict:
    """``ViewerFact`` → API 投影 dict。"""
    return {
        "id": fact.fact_id,
        "platform": fact.platform,
        "user_id": fact.user_id,
        "fact_text": fact.fact_text,
        "source_message_id": fact.source_message_id,
        "created_at_ms": fact.created_at_ms,
    }


@router.delete("/facts/{fact_id}", response_model=MutationResponse)
async def delete_viewer_fact(fact_id: int, server: ServerDep) -> MutationResponse:
    """删除单条事实；id 不存在时 404。"""
    memory = _require_memory(server)
    deleted = await memory.delete_viewer_fact(fact_id=fact_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"事实不存在: {fact_id}")
    return MutationResponse(success=True)
