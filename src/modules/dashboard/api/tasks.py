"""任务卡只读快照端点

游戏 Agent 委派任务的 WebUI 消费面：进行中读任务记录表（TaskLedger
内存账本），已完结从事件历史环形缓冲聚合——``task.changed`` 不落库
（StorageLedger 无此订阅），故"历史"仅本次运行内成立，重启即清零；
跨重启历史等回看事实持久化线定案后自然获得，本端点不臆造数据。

- ``GET /tasks``  全量快照（进行中 + 已完结）

数据通道与 WS 补订阅 ``task.changed`` 配套：打开页面拉一次快照，
实时增量走 WS 推送。
"""

from typing import TYPE_CHECKING, Annotated, Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.events.event_history import DEFAULT_MAX_EVENTS
from src.modules.events.names import CoreEvents
from src.modules.logging import get_logger
from src.modules.tools.tasks import TERMINAL_TASK_STATES

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

logger = get_logger("DashboardTasksAPI")

router = APIRouter()

# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


class TaskCard(BaseModel):
    """单张任务卡（记录表行或事件流末态 payload 的投影）。"""

    task_id: str
    instruction: str = ""  # 指令摘要（snapshot.instruction；事件聚合取末态快照）
    status: str
    initiator: str = ""
    executor: str = ""
    summary: str = ""  # 最近一次变化摘要（仅事件聚合侧有；账本侧为空）
    created_at_ms: int = 0
    updated_at_ms: int = 0


class TaskSnapshotResponse(BaseModel):
    """任务卡全量快照。"""

    running: List[TaskCard] = []
    finished: List[TaskCard] = []


def _require_task_tracker(server: "DashboardServer") -> Any:
    """取任务跟踪器，未装配时 503。"""
    tracker = getattr(server, "task_tracker", None)
    if tracker is None:
        raise HTTPException(status_code=503, detail="任务基建未装配，任务卡不可用")
    return tracker


def _card_from_record(record: Any) -> TaskCard:
    """账本记录 → 进行中任务卡。"""
    snapshot = record.snapshot if isinstance(record.snapshot, dict) else {}
    return TaskCard(
        task_id=record.task_id,
        instruction=str(snapshot.get("instruction", "") or ""),
        status=str(record.status),
        initiator=str(record.initiator),
        executor=str(record.executor),
        created_at_ms=int(record.created_at_ms),
        updated_at_ms=int(record.updated_at_ms),
    )


def _finished_cards_from_history(server: "DashboardServer") -> List[TaskCard]:
    """从事件环聚合已完结任务卡（按 task_id 取末态 payload；仅本次运行内）。"""
    history = getattr(server, "event_history", None)
    if history is None:
        return []
    last_by_task: Dict[str, Any] = {}
    try:
        # 环形缓冲容量即全量上界（5000）；type 直通 = 事件名（room.message 折叠规则不涉及）
        records = history.query(types=[CoreEvents.TASK_CHANGED], limit=DEFAULT_MAX_EVENTS)
    except Exception as exc:  # noqa: BLE001 - 事件环读取失败按无历史降级
        logger.warning(f"事件历史聚合任务卡失败（按空历史处理）: {exc}")
        return []
    for record in records:
        data = record.data or {}
        task_id = str(data.get("task_id", "") or "")
        if task_id:
            # query 结果最新在前：首个出现的即该任务末态 payload
            last_by_task.setdefault(task_id, data)
    cards: List[TaskCard] = []
    for task_id, data in last_by_task.items():
        status = str(data.get("status", "") or "")
        if status not in TERMINAL_TASK_STATES:
            continue  # 非终态属进行中——账本快照已覆盖，避免双份
        snapshot = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else {}
        cards.append(
            TaskCard(
                task_id=task_id,
                instruction=str(snapshot.get("instruction", "") or ""),
                status=status,
                initiator=str(data.get("initiator", "") or ""),
                executor=str(data.get("executor", "") or ""),
                summary=str(data.get("summary", "") or ""),
                updated_at_ms=int(data.get("timestamp_ms", 0) or 0),
            )
        )
    cards.sort(key=lambda c: c.updated_at_ms, reverse=True)
    return cards


@router.get("", response_model=TaskSnapshotResponse, summary="任务卡全量快照（进行中 + 已完结）")
async def get_tasks(server: ServerDep) -> TaskSnapshotResponse:
    """游戏 Agent 委派任务全景。

    进行中 = 任务记录表当前账面（含 accepted/running/waiting_for_decision）；
    已完结 = 事件历史环按 task_id 聚合的终态末次 payload——纯内存观察窗，
    仅本次运行内成立。已完结按更新时间倒序。
    """
    tracker = _require_task_tracker(server)
    ledger = tracker.ledger
    running = [_card_from_record(ledger.get(task_id)) for task_id in ledger.active_task_ids()]
    running = [card for card in running if card is not None]
    finished = _finished_cards_from_history(server)
    return TaskSnapshotResponse(running=running, finished=finished)
