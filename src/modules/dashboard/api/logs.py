"""日志历史查询 API

日志查看器的落盘历史检索面（``GET /api/v1/logs``）：扫描 LogStreamer
按天落盘的 JSONL（``logs/webui/``），支持时间窗、双向游标翻页、级别/
模块筛选与正文子串搜索。窗口起点缺省为本次启动时刻（前端"加载更早"
的默认范围），显式传 ``start_ms`` 可跨启动跳转（复盘用途）。

落盘默认仅含 INFO 及以上（DEBUG 只在内存尾部缓冲）——历史查询自然
不含 DEBUG 行，属既定落盘策略而非缺陷。文件扫描是同步 IO，放线程池
执行（低频诊断操作，无缓存）。
"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Optional

from fastapi import APIRouter, Depends, Query

from src.modules.dashboard.api.system import startup_time_ms
from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.logs import LogEntryItem, LogQueryResponse
from src.modules.dashboard.services.log_query import LogHistoryReader
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("LogsAPI")

ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


@router.get("/logs", response_model=LogQueryResponse)
async def query_logs(
    server: ServerDep,
    start_ms: Optional[int] = Query(None, ge=0, description="窗口起点（Unix 毫秒）；缺省=本次启动时刻"),
    end_ms: Optional[int] = Query(None, ge=0, description="窗口终点（Unix 毫秒）；缺省=当前时刻"),
    before_timestamp_ms: Optional[int] = Query(None, ge=0, description="游标：只返回严格早于该时刻的条目（向前翻页）"),
    levels: Optional[str] = Query(None, description="级别筛选，逗号分隔（如 WARNING,ERROR）"),
    modules: Optional[str] = Query(None, description="模块筛选，逗号分隔"),
    q: Optional[str] = Query(None, max_length=200, description="正文/模块子串搜索（大小写不敏感）"),
    take: str = Query(
        "newest", pattern="^(newest|oldest)$", description="取窗口内最靠新（newest）还是最靠旧（oldest）的一页"
    ),
    limit: int = Query(200, ge=1, le=500, description="最大返回条数"),
) -> LogQueryResponse:
    """查询落盘日志历史（时间升序窗口，双向游标翻页）。"""
    reader = LogHistoryReader(_persist_dir_of(server))
    effective_start = start_ms if start_ms is not None else startup_time_ms()
    effective_end = end_ms if end_ms is not None else now_ms()
    level_set = {level.strip().upper() for level in levels.split(",") if level.strip()} if levels else None
    module_set = {module.strip() for module in modules.split(",") if module.strip()} if modules else None
    keyword = q.strip() if q and q.strip() else None

    window = await asyncio.to_thread(
        reader.query,
        start_ms=effective_start,
        end_ms=effective_end,
        before_timestamp_ms=before_timestamp_ms,
        levels=level_set,
        modules=module_set,
        keyword=keyword,
        take=take,
        limit=limit,
    )
    return LogQueryResponse(
        entries=[LogEntryItem.model_validate(entry) for entry in window.entries],
        total=len(window.entries),
        has_more_older=window.has_more_older,
        has_more_newer=window.has_more_newer,
        persist_available=reader.persist_available,
        window_start_ms=effective_start,
    )


def _persist_dir_of(server: "DashboardServer") -> Optional[Path]:
    """从装配取日志落盘目录；LogStreamer 未装配/未启用落盘时 None（历史降级为空）。"""
    streamer = server.log_streamer
    if streamer is None:
        return None
    return streamer.persist_dir
