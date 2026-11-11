"""后台任务查询端点：进程内"还在跑的长任务"列表（WebUI 底栏任务条的数据接口）。

数据源是 ``BackgroundTaskRegistry``（进程级单例，见 ``src/modules/background/``）：
登记处在主播 Agent 装配时构造并随任务登记/注销，本模块只读快照。登记处尚未装配
（进程刚启动、主播 Agent 还没装）时回空列表——不是错误，任务只会在装配后出现。
进行中状态的实时变化走 ``background.task.changed`` 事件推送，这里兜刷新恢复。
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter

from src.modules.background import get_registry
from src.modules.dashboard.schemas.background import BackgroundTaskItem, BackgroundTaskListResponse

router = APIRouter()


@router.get("", response_model=BackgroundTaskListResponse, summary="当前全部在途后台任务")
async def list_background_tasks() -> BackgroundTaskListResponse:
    """返回登记处当前快照；登记处未装配（主播 Agent 未启动）时回空列表。"""
    registry = get_registry()
    if registry is None:
        return BackgroundTaskListResponse(tasks=[])
    rows: List[BackgroundTaskItem] = [
        BackgroundTaskItem(
            task_id=info.task_id,
            title=info.title,
            state=info.state,
            progress_text=info.progress_text,
            started_at_ms=info.started_at_ms,
            output_chars=info.output_chars,
            output_tail=info.output_tail,
        )
        for info in registry.snapshot()
    ]
    return BackgroundTaskListResponse(tasks=rows)


__all__ = ["router"]
