"""background.task.changed 事件 Payload（进程内后台长任务可见性）

唯一发布者是 ``BackgroundTaskRegistry``（``src/modules/background/``）：登记 /
更新进度 / 注销时各发一条，载荷带该任务完整现状。终态（finished / failed）
任务随即从注册表移除——事件是前端弹通知与摘行的信号，注册表查询口才是
"现在有哪些任务"的事实源。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event

# 任务状态的取值全集：running 进行中 / finished 已完成 / failed 失败。
# 终态事件照发，但任务已从注册表移除。
BackgroundTaskState = Literal["running", "finished", "failed"]


@register_event(CoreEvents.BACKGROUND_TASK_CHANGED)
class BackgroundTaskChangedPayload(BasePayload):
    """一条后台任务的现状（background.task.changed）。

    Attributes:
        task_id: 任务号（登记方自定，如 "settle-58" / "memory-index-backfill"）
        title: 给人看的标题（如 "整理场次：2026-11-06 周五 22:22"）
        state: 任务状态（running / finished / failed）
        progress_text: 进度自由文本（如 "第 2 笔/共 3 笔" / "还缺 320 行"）
        started_at_ms: 开始时刻（Unix 毫秒）
        failure_reason: 失败原因（仅 failed 时带）
        output_chars: 当前这笔模型调用已输出的正文字数（仅模型调用期间大于 0）
        output_tail: 当前这笔模型调用最近输出的文字尾巴（浮层实时预览用，非模型调用期间为空）
    """

    task_id: str = Field(..., description="任务号（登记方自定，同一任务的历次变化用它关联）")
    title: str = Field(..., description="给人看的标题")
    state: BackgroundTaskState = Field(..., description="任务状态（running / finished / failed）")
    progress_text: str = Field(default="", description="进度自由文本（不造百分比，怎么自然怎么写）")
    started_at_ms: int = Field(..., description="任务开始时刻（Unix 毫秒）")
    failure_reason: Optional[str] = Field(default=None, description="失败原因（仅 failed 时带）")
    output_chars: int = Field(default=0, description="当前这笔模型调用已输出的正文字数（仅流式调用期间大于 0）")
    output_tail: str = Field(default="", description="当前这笔模型调用最近输出的文字尾巴（非模型调用期间为空）")


__all__ = ["BackgroundTaskChangedPayload", "BackgroundTaskState"]
