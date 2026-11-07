"""BackgroundTaskRegistry——进程级单例的后台任务登记处。

任务行只存"现在还在跑的"：begin 登记、update 报进度、finish 注销（终态行不
留存——终态事实各有归属，如整理有 settled_at 落库；历史由事件查看器与应用
日志兜底）。每次变更都发 ``background.task.changed``（载荷带该任务完整现状），
前端收到终态事件后把行摘掉并弹通知。

查询口（``GET /api/v1/background-tasks``）与装配方共用这里的模块级访问器：
``setup_registry(event_bus)`` 在装配时构造注入事件总线，``get_registry()``
返回当前实例（未装配时为 None，查询端点据此回空列表）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.background import BackgroundTaskChangedPayload, BackgroundTaskState
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms

__all__ = ["BackgroundTaskInfo", "BackgroundTaskRegistry", "get_registry", "setup_registry"]

logger = get_logger("BackgroundTaskRegistry")


@dataclass
class BackgroundTaskInfo:
    """一条在途任务的现状（登记处的内存行，也是查询口的响应行）。"""

    task_id: str
    title: str
    state: BackgroundTaskState
    progress_text: str
    started_at_ms: int


class BackgroundTaskRegistry:
    """登记 / 更新进度 / 注销三个动作，每次变更广播一条任务事件。

    同一 task_id 重复 begin 视为调用方自相矛盾，记 warning 后按重新开始覆盖
    ——登记处不能因为一个写坏的调用方把整理与索引挡住。
    """

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._tasks: Dict[str, BackgroundTaskInfo] = {}

    async def begin(self, task_id: str, title: str, *, progress_text: str = "") -> None:
        """登记一条任务并广播 running。"""
        if task_id in self._tasks:
            logger.warning(f"任务 {task_id} 重复登记（旧行未注销），按重新开始覆盖")
        info = BackgroundTaskInfo(
            task_id=task_id, title=title, state="running", progress_text=progress_text, started_at_ms=now_ms()
        )
        self._tasks[task_id] = info
        await self._emit(info)

    async def update(self, task_id: str, progress_text: str) -> None:
        """更新一条在途任务的进度文本并广播 running。"""
        info = self._tasks.get(task_id)
        if info is None:
            logger.warning(f"任务 {task_id} 未登记就报进度，忽略（progress_text={progress_text}）")
            return
        info.progress_text = progress_text
        await self._emit(info)

    async def finish(self, task_id: str, *, success: bool, failure_reason: Optional[str] = None) -> None:
        """把任务标成终态并广播，随后从登记处移除（终态行不留存）。"""
        info = self._tasks.pop(task_id, None)
        if info is None:
            logger.warning(f"任务 {task_id} 未登记就注销，忽略（success={success}）")
            return
        state: BackgroundTaskState = "finished" if success else "failed"
        final_info = BackgroundTaskInfo(
            task_id=info.task_id,
            title=info.title,
            state=state,
            progress_text=info.progress_text,
            started_at_ms=info.started_at_ms,
        )
        await self._emit(final_info, failure_reason=failure_reason if not success else None)

    def snapshot(self) -> List[BackgroundTaskInfo]:
        """当前全部在途任务（查询口用；行序按登记先后）。"""
        return list(self._tasks.values())

    async def _emit(self, info: BackgroundTaskInfo, *, failure_reason: Optional[str] = None) -> None:
        """广播一条任务现状；广播失败只记日志，登记处自身的状态已经落定。"""
        try:
            await self._event_bus.emit(
                CoreEvents.BACKGROUND_TASK_CHANGED,
                BackgroundTaskChangedPayload(
                    task_id=info.task_id,
                    title=info.title,
                    state=info.state,
                    progress_text=info.progress_text,
                    started_at_ms=info.started_at_ms,
                    failure_reason=failure_reason,
                ),
                source="BackgroundTaskRegistry",
            )
        except Exception as exc:  # noqa: BLE001 - 广播故障不能拖垮登记方（整理/索引照常跑）
            logger.error(f"广播任务 {info.task_id} 现状失败: {exc}", exc=exc)


# 模块级单例：装配方 setup 一次，查询口与各任务方 get 读取
_registry: Optional[BackgroundTaskRegistry] = None


def setup_registry(event_bus: EventBus) -> BackgroundTaskRegistry:
    """构造登记处单例（装配入口，进程内只应调用一次）。"""
    global _registry
    if _registry is not None:
        logger.warning("BackgroundTaskRegistry 已装配过，重复 setup 返回既有实例")
        return _registry
    _registry = BackgroundTaskRegistry(event_bus)
    return _registry


def get_registry() -> Optional[BackgroundTaskRegistry]:
    """当前登记处实例；尚未装配时为 None（进程还没装配主播 Agent）。"""
    return _registry
