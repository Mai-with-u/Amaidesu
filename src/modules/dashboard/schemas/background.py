"""后台任务查询端点的响应模型（只读：登记处现状的 JSON 镜像）。"""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field


class BackgroundTaskItem(BaseModel):
    """一条在途后台任务的现状行。"""

    task_id: str = Field(..., description="任务号（如 settle-58 / memory-index-backfill）")
    title: str = Field(..., description="给人看的标题")
    state: Literal["running", "finished", "failed"] = Field(..., description="任务状态（查询口只见 running）")
    progress_text: str = Field(default="", description="进度自由文本（如 第 2 笔/共 3 笔）")
    started_at_ms: int = Field(..., description="任务开始时刻（Unix 毫秒）")


class BackgroundTaskListResponse(BaseModel):
    """当前全部在途后台任务（终态行不在注册表里，历史靠事件与应用日志）。"""

    tasks: List[BackgroundTaskItem] = Field(default_factory=list, description="按登记先后排列的在途任务")


__all__ = ["BackgroundTaskItem", "BackgroundTaskListResponse"]
