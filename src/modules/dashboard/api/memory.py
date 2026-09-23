"""记忆管理端点（WebUI 记忆管理页数据面）

``_memory_facts`` 模块私有表的管理消费面：列表检索 / 手工增改 / 单条删除 /
召回测试 / 总量统计。读写全部经 ``SimpleMemory`` 管理面方法（私有表契约：
仅 SimpleMemory 触碰该表），本模块不做 SQL。

- ``GET  /memory/stats``         总量统计（总数 / 各来源计数 / 最新写入）
- ``GET  /memory/facts``         条目列表（搜索 / 排序 / 分页，``total`` 全计数）
- ``POST /memory/facts``         手工新增（source 固定 "webui"）
- ``PATCH /memory/facts/{id}``   部分更新（text / tags / importance，缺省不改）
- ``DELETE /memory/facts/{id}``  删除单条（id 不存在 404）
- ``POST /memory/recall``        召回测试（与 Agent 侧 query_memory 同链路）

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


def _split_tags(tags: str) -> List[str]:
    """存储态逗号串 → 展示态列表（空串 → 空列表）。"""
    return [t for t in tags.split(",") if t]


class MemoryFactItem(BaseModel):
    """单条记忆（``_memory_facts`` 行投影；tags 已拆为列表）。"""

    id: int
    text: str
    source: str
    tags: List[str]
    importance: int
    timestamp_ms: int


class MemoryFactListResponse(BaseModel):
    """条目列表响应：``total`` 为命中搜索条件的全量行数（分页器用）。"""

    total: int
    items: List[MemoryFactItem]


class MemoryFactCreateRequest(BaseModel):
    """手工新增请求（source 由服务端固定为 "webui"）。"""

    text: str = Field(min_length=1, description="记忆文本（空白串拒绝）")
    tags: List[str] = Field(default_factory=list, description="标签列表（逗号连接落库）")
    importance: int = Field(default=0, ge=0, description="重要度（召回排序权重）")


class MemoryFactCreateResponse(BaseModel):
    """新增响应：``accepted=False`` 携带拒绝原因（如空文本）。"""

    memory_id: int
    accepted: bool
    message: str = ""


class MemoryFactUpdateRequest(BaseModel):
    """部分更新请求：``None`` 字段保持不变；``tags`` 传空列表即清空。"""

    text: Optional[str] = Field(default=None, min_length=1)
    tags: Optional[List[str]] = None
    importance: Optional[int] = Field(default=None, ge=0)


class MemoryMutationResponse(BaseModel):
    """更新 / 删除的通用响应（``success=False`` 多为 id 不存在）。"""

    success: bool


class MemorySourceCount(BaseModel):
    """单来源条目计数。"""

    source: str
    count: int


class MemoryStatsResponse(BaseModel):
    """总量统计：来源计数按条数降序；空库 ``latest_ms=0``。"""

    total_facts: int
    sources: List[MemorySourceCount]
    latest_ms: int


class MemoryRecallRequest(BaseModel):
    """召回测试请求（与 Agent 侧 query_memory 工具同参语义）。"""

    query: str = Field(min_length=1, description="查询文本/关键词")
    top_k: int = Field(default=5, ge=1, le=20)


class MemoryRecallHit(BaseModel):
    """单条召回命中（``score`` 越大越相关）。"""

    memory_id: int
    text: str
    score: float
    timestamp_ms: int
    source: str
    tags: List[str]


class MemoryRecallResponse(BaseModel):
    """召回测试响应（空匹配时 ``hits`` 为空列表）。"""

    query: str
    hits: List[MemoryRecallHit]


@router.get("/stats", response_model=MemoryStatsResponse)
async def get_memory_stats(server: ServerDep) -> MemoryStatsResponse:
    """记忆库总量统计（管理页头部概览）。"""
    memory = _require_memory(server)
    stats = await memory.stats()
    return MemoryStatsResponse(
        total_facts=stats.total_facts,
        sources=[{"source": s, "count": c} for s, c in stats.sources],
        latest_ms=stats.latest_ms,
    )


@router.get("/facts", response_model=MemoryFactListResponse)
async def list_memory_facts(
    server: ServerDep,
    search: str = Query(default="", max_length=200, description="子串搜索 text/source/tags"),
    order_by: str = Query(default="timestamp_ms", description="timestamp_ms | importance"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> MemoryFactListResponse:
    """条目列表（搜索 / 排序白名单 / 分页）。"""
    memory = _require_memory(server)
    total, facts = await memory.list_facts(
        search=search,
        order_by=order_by,
        limit=limit,
        offset=offset,
    )
    return MemoryFactListResponse(
        total=total,
        items=[
            {
                "id": f.memory_id,
                "text": f.text,
                "source": f.source,
                "tags": _split_tags(f.tags),
                "importance": f.importance,
                "timestamp_ms": f.timestamp_ms,
            }
            for f in facts
        ],
    )


@router.post("/facts", response_model=MemoryFactCreateResponse)
async def create_memory_fact(
    server: ServerDep,
    request: MemoryFactCreateRequest,
) -> MemoryFactCreateResponse:
    """手工新增一条记忆（来源记为 "webui"，与 Agent 写入区分）。"""
    memory = _require_memory(server)
    result = await memory.ingest(
        request.text,
        source="webui",
        tags=request.tags,
        importance=request.importance,
    )
    return MemoryFactCreateResponse(
        memory_id=result.memory_id,
        accepted=result.accepted,
        message=result.message,
    )


@router.patch("/facts/{memory_id}", response_model=MemoryMutationResponse)
async def update_memory_fact(
    server: ServerDep,
    memory_id: int,
    request: MemoryFactUpdateRequest,
) -> MemoryMutationResponse:
    """部分更新（缺省字段保持不变）；无更新字段 / 空白文本 422，id 不存在 404。"""
    memory = _require_memory(server)
    if request.text is None and request.tags is None and request.importance is None:
        raise HTTPException(status_code=422, detail="缺少任何更新字段（text/tags/importance）")
    if request.text is not None and not request.text.strip():
        raise HTTPException(status_code=422, detail="text 不能为空白")
    updated = await memory.update_fact(
        memory_id,
        text=request.text,
        tags=request.tags,
        importance=request.importance,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"记忆条目不存在: {memory_id}")
    return MemoryMutationResponse(success=True)


@router.delete("/facts/{memory_id}", response_model=MemoryMutationResponse)
async def delete_memory_fact(
    server: ServerDep,
    memory_id: int,
) -> MemoryMutationResponse:
    """删除单条记忆；id 不存在时 404。"""
    memory = _require_memory(server)
    deleted = await memory.delete_fact(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"记忆条目不存在: {memory_id}")
    return MemoryMutationResponse(success=True)


@router.post("/recall", response_model=MemoryRecallResponse)
async def recall_memory(
    server: ServerDep,
    request: MemoryRecallRequest,
) -> MemoryRecallResponse:
    """召回测试：走与 Agent 侧 ``query_memory`` 工具相同的 ``recall`` 链路。"""
    memory = _require_memory(server)
    hits = await memory.recall(request.query, top_k=request.top_k)
    return MemoryRecallResponse(
        query=request.query,
        hits=[
            {
                "memory_id": h.memory_id,
                "text": h.text,
                "score": h.score,
                "timestamp_ms": h.timestamp_ms,
                "source": str(h.metadata.get("source", "")),
                "tags": _split_tags(str(h.metadata.get("tags", ""))),
            }
            for h in hits
        ],
    )
