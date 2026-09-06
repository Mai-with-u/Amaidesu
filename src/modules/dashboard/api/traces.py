"""
Trace 聚合 API

从 EventHistoryService 中按 message_id 聚合三段链路：
- messages: room.message.* 事件（采集器行为流）
- planning: planner.checkpoint / agenda.update 事件（决策与编排）
- execution: tool.result.* 事件（工具调用结果回传）

不依赖额外存储，纯查询 EventHistoryService 内存环形缓冲。
注意：事件类型字符串必须与 EventHistoryRecorder 中写入 EventRecord.type
的字面量保持一致。

----------------------------------------------------------------------
链路键说明（诚实实现，不伪造数据）：

``BasePayload.id``（uuid4，model_dump → model_validate 分发全程稳定）是
room.message 事件的天然链路键——``EventRecord.id``、``EventRecord.data["id"]``
与 WS 消息 id 三者同源。messages 段据此实现按 ``message_id`` 精确对齐。

planning / execution 段仍为空数组：``AgendaPayload`` / ``CheckpointPayload`` /
``ToolResultPayload`` 的事件 id 标识的是各自事件，与触发消息之间没有关联键
（已查 src/modules/events/payloads/ 内 planner.py / agenda.py / tool_result.py
验证）。待事件契约扩展关联字段后，仅需修改 ``_collect_segments`` 的两个空段
即可启用三段对齐。
----------------------------------------------------------------------
"""

from typing import TYPE_CHECKING, Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.events.event_type_map import ROOM_MESSAGE_TYPE
from src.modules.events.event_history import EventHistoryService, EventRecord
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("TracesAPI")

# 三段聚合的事件类型集合（与 EventHistoryRecorder 写入字面量保持一致）
_PLANNING_EVENT_TYPES = frozenset({"planner.checkpoint", "agenda.update"})
_EXECUTION_EVENT_PREFIX = "tool.result."

ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


@router.get("/traces")
async def list_traces(
    limit: int = Query(20, ge=1, le=100, description="返回最大 Trace 数"),
    server: ServerDep = ...,
):
    """获取最近 Trace 列表。

    策略: 从最近的 ``room.message`` 事件中提取 ``message_id``,
    然后对每个 ``message_id`` 聚合完整链路。

    Args:
        limit: 返回的最大 Trace 数(同时也是回溯的消息事件数)。
        server: 由 FastAPI 注入的 DashboardServer。

    Returns:
        包含 ``traces`` 列表和 ``total`` 计数的字典。
    """
    history: Optional[EventHistoryService] = server.event_history
    if not history:
        return {"traces": [], "total": 0}

    received = history.query(types=[ROOM_MESSAGE_TYPE], limit=limit)

    traces: List[Dict[str, Any]] = []
    for event in received:
        message_id = _extract_message_id_from_received(event)
        if not message_id:
            continue
        trace = _build_trace(history, message_id)
        if trace:
            traces.append(trace)

    return {"traces": traces, "total": len(traces)}


@router.get("/traces/{message_id}")
async def get_trace(
    message_id: str,
    server: ServerDep = ...,
):
    """获取单条消息的完整链路追踪。

    Args:
        message_id: 目标 NormalizedMessage 的 ``message_id``。
        server: 由 FastAPI 注入的 DashboardServer。

    Returns:
        包含 ``trace`` 字段的字典;若未找到则 ``trace=None`` 并附带 ``error``。
    """
    history: Optional[EventHistoryService] = server.event_history
    if not history:
        return {"trace": None, "error": "EventHistoryService 未启用"}

    trace = _build_trace(history, message_id)
    if not trace:
        return {"trace": None, "error": f"未找到 message_id={message_id} 的链路"}

    return {"trace": trace}


def _build_trace(history: EventHistoryService, message_id: str) -> Optional[Dict[str, Any]]:
    """从 EventHistoryService 构建单条消息的 Trace 聚合数据（v2 三段聚合）。

    Returns:
        聚合的 Trace 字典;若 room.message 事件未找到则返回 ``None``。

    Notes:
        见模块顶部"链路键说明"——messages 段按链路键精确对齐；
        planning/execution 段因载荷间无关联键暂返回空数组（不伪造）。
    """
    msg_event = _find_room_message_event(history, message_id)
    if not msg_event:
        return None

    # v2 扁平 RoomMessagePayload：content / message_type / user{...} / live_session_id
    flat = msg_event.data if isinstance(msg_event.data, dict) else {}
    user = flat.get("user") if isinstance(flat.get("user"), dict) else {}

    segments = _collect_segments(history, message_id)

    trace: Dict[str, Any] = {
        "message_id": message_id,
        "message": {
            "text": flat.get("content", ""),
            "source": flat.get("live_session_id", ""),
            "data_type": flat.get("message_type", ""),
            "timestamp_ms": flat.get("timestamp_ms", 0),
            "user_id": user.get("id"),
            "user_nickname": user.get("name"),
        },
        "event": {
            "name": msg_event.type,
            "timestamp": msg_event.timestamp,
        },
        "segments": segments,
    }

    return trace


def _collect_segments(
    history: EventHistoryService,
    message_id: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """按 message_id 聚合三段记录。

    - messages 段：按链路键（payload 顶层 ``id``）精确对齐的 room.message 记录。
    - planning 段：返回 []（CheckpointPayload/AgendaPayload 与触发消息无关联键）。
    - execution 段：返回 []（ToolResultPayload 与触发消息无关联键）。

    待事件契约扩展跨事件关联字段后，仅替换本函数 planning/execution 两个
    空段的过滤逻辑即可启用三段对齐，无需修改 ``_build_trace`` 调用方。
    """
    records = _iter_recent(history)

    messages: List[Dict[str, Any]] = []
    for record in records:
        if record.type != ROOM_MESSAGE_TYPE:
            continue
        if _extract_message_id(record.data) != message_id:
            continue
        messages.append(_serialize_segment_record(record))

    return {
        "messages": messages,
        "planning": [],
        "execution": [],
    }


def _serialize_segment_record(record: EventRecord) -> Dict[str, Any]:
    """把 EventRecord 序列化为前端消费的字典（轻量拷贝，避免泄露内部对象）。

    ``timestamp_ms`` 由 ``timestamp``（Unix 秒）换算，前端统一消费毫秒。
    """
    return {
        "id": record.id,
        "type": record.type,
        "timestamp": record.timestamp,
        "timestamp_ms": int(record.timestamp * 1000),
        "level": record.level,
        "source": record.source,
        "summary": record.summary,
        "data": dict(record.data) if isinstance(record.data, dict) else {},
    }


def _iter_recent(history: EventHistoryService) -> List[EventRecord]:
    """获取环形缓冲中的所有事件（最新在前，浅拷贝）。"""
    return list(history.get_recent(history.max_events))


def _find_room_message_event(
    history: EventHistoryService,
    message_id: str,
) -> Optional[EventRecord]:
    """在 EventHistoryService 中查找匹配 ``message_id`` 的 room.message 事件。

    直接遍历环形缓冲（最大 5000 条，Dashboard 页面足够快），
    因为需要在 payload 内嵌套字段（如 ``message.message_id``）上过滤，
    现有 ``query()`` 接口不支持。
    """
    for record in _iter_recent(history):
        if record.type != ROOM_MESSAGE_TYPE:
            continue
        candidate_id = _extract_message_id(record.data)
        if candidate_id == message_id:
            return record
    return None


def _extract_message_id_from_received(event: EventRecord) -> str:
    """从 ``room.message`` 事件记录中提取链路键。"""
    return _extract_message_id(event.data)


def _extract_message_id(event_data: Any) -> str:
    """从 ``room.message`` 事件的 data 字典中提取链路键。

    链路键 = 扁平 payload 的顶层 ``id``（``BasePayload.id`` uuid4，与
    ``EventRecord.id`` / WS 消息 id 同源）。

    Args:
        event_data: ``EventRecord.data`` 字典或可空值。

    Returns:
        链路键;无法提取时返回空字符串。
    """
    if not isinstance(event_data, dict):
        return ""
    mid = event_data.get("id", "")
    return mid if isinstance(mid, str) else ""
