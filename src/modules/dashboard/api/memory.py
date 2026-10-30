"""主播记忆管理端点（WebUI 主播记忆页的数据接口）

``viewer_profiles`` 表的管理接口：印象列表 / **人工编辑纠正** / 删除 / 统计；
外加长期记忆文件（MEMORY.md，正式与彩排各一份）的查看与整份编辑。
读写全部经 ``ViewerProfileRepo``，本模块不做 SQL。印象由主播 Agent 在收播时
生成（见 ``src/agents/streamer/memory/``），这里只提供查看与纠正。

- ``GET  /memory/stats``                               总量统计（常客数 / 最近更新）
- ``GET  /memory/profiles``                            画像列表（搜索 / 分页）
- ``PATCH /memory/profiles/{viewer_id}``               人工纠正印象文本
- ``DELETE /memory/profiles/{viewer_id}``              删除印象（删除后该观众回到无印象态）
- ``GET  /memory/long-term/{kind}``                   读长期记忆全文（kind = live / rehearsal）
- ``PUT  /memory/long-term/{kind}``                   整份覆盖写长期记忆（空文本 = 清空）
- ``GET  /memory/long-term/{kind}/history``           留底记录（新的在前：来由、场次、逐条改动）
- ``GET  /memory/long-term/{kind}/history/{entry_id}`` 某条记录写之前的全文
- ``POST /memory/long-term/{kind}/history/{entry_id}/restore`` 恢复到这条记录写之前的版本

画像以内部观众 ID 为键；列表行带观众的平台与当前昵称，供页面直接展示。

长期记忆的编辑从下一次开播起生效：开播时整份读出、整场不变。每次写入（收播整理、
页面保存、恢复旧版本）都由存储留底，页面可以查看改了什么、把任意一次改动撤回。
页面保存不需要懂笔记格式：下一次收播整理会对照上一次整理写下的版本，把人改过的
行记成人工条目。

未注入仓储（极简启动 / 部分测试装配）时端点返回 503。
"""

from typing import TYPE_CHECKING, Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.modules.dashboard.dependencies import get_dashboard_server, server_component

from src.modules.types.session_kind import SessionKind

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer
    from src.modules.storage.long_memory import LongMemoryStore
    from src.modules.storage.repos import ViewerProfileRepo

router = APIRouter()

# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


# 组件访问声明：观众画像仓储未装配（极简启动/测试）时统一 503
ViewerProfileRepoDep = Annotated[
    "ViewerProfileRepo", Depends(server_component("viewer_profile_repo", missing_detail="观众画像不可用"))
]


# 长期记忆文件存储未装配（极简启动/测试）时统一 503
LongMemoryStoreDep = Annotated[
    "LongMemoryStore", Depends(server_component("long_memory_store", missing_detail="长期记忆不可用"))
]


class LongMemoryResponse(BaseModel):
    """一份长期记忆的全文与文件位置（文件不存在时正文为空、修改时刻为 0）。"""

    kind: SessionKind = Field(description="场次类型：live=正式 / rehearsal=彩排")
    text: str = Field(description="全文（去首尾空白）")
    path: str = Field(description="文件绝对路径（可直接用编辑器打开）")
    updated_at_ms: int = Field(description="文件最近修改时刻（Unix 毫秒）；文件不存在为 0")


class LongMemoryUpdateRequest(BaseModel):
    """整份覆盖写：人工编辑后的全文；空串 = 清空这份记忆。"""

    text: str = Field(default="", max_length=20_000, description="新全文")


class LongMemoryHistoryItem(BaseModel):
    """一条留底记录：这次为什么写、改了什么；``has_snapshot`` 为能否恢复到这次之前。"""

    id: str = Field(description="记录编号")
    at_ms: int = Field(description="写入时刻（Unix 毫秒）")
    reason: str = Field(description="来由：收播整理 / WebUI 保存 / 恢复旧版本")
    session_id: Optional[int] = Field(description="收播整理时的场次主键；其余为空")
    changes: List[str] = Field(description="逐条改动说明（收播整理才有）")
    has_snapshot: bool = Field(description="是否留了写之前的全文（第一次写入之前没有文件）")


class LongMemoryHistoryResponse(BaseModel):
    """一份长期记忆的留底记录（新的在前）。"""

    kind: SessionKind = Field(description="场次类型：live=正式 / rehearsal=彩排")
    items: List[LongMemoryHistoryItem] = Field(description="留底记录")


class LongMemorySnapshotResponse(BaseModel):
    """某条留底记录写之前的全文。"""

    kind: SessionKind = Field(description="场次类型")
    entry_id: str = Field(description="记录编号")
    text: str = Field(description="这条记录写之前的全文")


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
    latest = rows[0].updated_at_ms if rows else 0
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
                viewer_id=row.viewer_id,
                platform=row.platform,
                user_name=row.user_name,
                profile_text=row.profile_text,
                last_compressed_at_ms=row.last_compressed_at_ms,
                updated_at_ms=row.updated_at_ms,
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


async def _long_memory_response(store: "LongMemoryStore", kind: SessionKind) -> LongMemoryResponse:
    path = store.path_of(kind)
    text = await store.read(kind)
    updated_at_ms = int(path.stat().st_mtime * 1000) if path.exists() else 0
    return LongMemoryResponse(kind=kind, text=text, path=str(path), updated_at_ms=updated_at_ms)


@router.get("/long-term/{kind}", response_model=LongMemoryResponse)
async def get_long_memory(kind: SessionKind, store: LongMemoryStoreDep) -> LongMemoryResponse:
    """读一份长期记忆（正式或彩排）；文件还没生成时返回空正文。"""
    return await _long_memory_response(store, kind)


@router.put("/long-term/{kind}", response_model=LongMemoryResponse)
async def put_long_memory(
    kind: SessionKind, request: LongMemoryUpdateRequest, store: LongMemoryStoreDep
) -> LongMemoryResponse:
    """整份覆盖写一份长期记忆并留底；下一次开播起生效（开播时整份读出、整场不变）。"""
    await store.write(kind, request.text, reason="WebUI 保存")
    return await _long_memory_response(store, kind)


@router.get("/long-term/{kind}/history", response_model=LongMemoryHistoryResponse)
async def list_long_memory_history(
    kind: SessionKind, store: LongMemoryStoreDep, limit: int = Query(50, ge=1, le=200)
) -> LongMemoryHistoryResponse:
    """一份长期记忆的留底记录（新的在前）。"""
    entries = await store.list_history(kind, limit=limit)
    return LongMemoryHistoryResponse(
        kind=kind, items=[LongMemoryHistoryItem.model_validate(entry.model_dump()) for entry in entries]
    )


@router.get("/long-term/{kind}/history/{entry_id}", response_model=LongMemorySnapshotResponse)
async def get_long_memory_snapshot(
    kind: SessionKind, entry_id: str, store: LongMemoryStoreDep
) -> LongMemorySnapshotResponse:
    """某条记录写之前的全文；记录不存在或没有快照时 404。"""
    text = await store.read_snapshot(kind, entry_id)
    if text is None:
        raise HTTPException(status_code=404, detail=f"没有记录 {entry_id} 之前的版本")
    return LongMemorySnapshotResponse(kind=kind, entry_id=entry_id, text=text)


@router.post("/long-term/{kind}/history/{entry_id}/restore", response_model=LongMemoryResponse)
async def restore_long_memory(kind: SessionKind, entry_id: str, store: LongMemoryStoreDep) -> LongMemoryResponse:
    """把长期记忆恢复到某条记录写之前的版本（恢复本身也留底，可再撤回）；没有快照时 404。"""
    if await store.restore(kind, entry_id) is None:
        raise HTTPException(status_code=404, detail=f"没有记录 {entry_id} 之前的版本")
    return await _long_memory_response(store, kind)
