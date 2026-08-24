"""
Trace 聚合 API（Wave 8 简化）

从 EventHistoryService 中查询并聚合消息全链路追踪数据。

链路构成（v2 行为流）：
- Input/Collector: EventRecord(type="room.message", data.message.message_id)
  — v2 中所有消息事件统一归并到 room.message.* 一族
- Agent/Tool 调用：通过 tool.result.* 与 Agenda/Planner 事件间接观测
  — v2 删除了 decision.intent / output.render 的 EventRecord 写入路径
  （Stage-glue 胶水事件不再使用，§1.46 定案）

不依赖额外存储，纯查询 EventHistoryService 内存环形缓冲。
注意: 事件类型字符串必须与 EventHistoryRecorder 中写入 EventRecord.type 的字面量保持一致。
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

# EventRecord.data 字典下承载业务负载的键名
_KEY_MESSAGE = "message"

ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


@router.get("/traces")
async def list_traces(
    limit: int = Query(20, ge=1, le=100, description="返回最大 Trace 数"),
    server: ServerDep = ...,
):
    """获取最近的 Trace 列表。

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
    """从 EventHistoryService 构建单条消息的 Trace 聚合数据。

    搜索策略（v2）：
    - room.message: data.message.message_id == message_id

    Returns:
        聚合的 Trace 字典;若 room.message 事件未找到则返回 ``None``。
    """
    msg_event = _find_event(history, ROOM_MESSAGE_TYPE, message_id, _KEY_MESSAGE)
    if not msg_event:
        return None

    msg_data = msg_event.data.get(_KEY_MESSAGE, {})
    if not isinstance(msg_data, dict):
        msg_data = {}

    trace: Dict[str, Any] = {
        "message_id": message_id,
        "message": {
            "text": msg_data.get("text", "") or msg_data.get("content", ""),
            "source": msg_data.get("source", ""),
            "data_type": msg_data.get("data_type", ""),
            "timestamp_ms": msg_data.get("timestamp_ms", 0),
            "user_id": msg_data.get("user_id"),
            "user_nickname": msg_data.get("user_nickname") or msg_data.get("user_name"),
        },
        "event": {
            "name": msg_event.data.get("event"),
            "timestamp": msg_event.timestamp,
        },
    }

    return trace


def _find_event(
    history: EventHistoryService,
    event_type: str,
    message_id: str,
    data_key: str,
) -> Optional[EventRecord]:
    """在 EventHistoryService 中查找匹配 ``message_id`` 的首条事件。

    直接遍历环形缓冲(最大 5000 条,管理页面足够快),
    因为需要在 payload 内嵌套字段(如 ``message.message_id``)上过滤,
    现有 ``query()`` 接口不支持。

    Args:
        history: EventHistoryService 实例。
        event_type: 要匹配的事件类型(如 ``room.message``)。
        message_id: 目标 ``message_id``。
        data_key: EventRecord.data 字典中承载业务负载的键名。

    Returns:
        首条匹配的事件;未找到则返回 ``None``。
    """
    for event in history.get_recent(history.max_events):
        if event.type != event_type:
            continue
        candidate_id = _extract_message_id(event.data.get(data_key, {}))
        if candidate_id == message_id:
            return event
    return None


def _extract_message_id_from_received(event: EventRecord) -> str:
    """从 ``room.message`` 事件中提取 ``message_id``。

    优先从 ``data.message.message_id`` 获取;若不存在则尝试 ``data.metadata.message_id``。
    """
    return _extract_message_id(event.data.get(_KEY_MESSAGE, {}))


def _extract_message_id(payload: Any) -> str:
    """从 ``room.message`` payload 中提取 ``message_id``。

    提取规则: ``payload.message_id``（v2 行为流统一字段）。

    Args:
        payload: EventRecord.data.message 字典或可空值。

    Returns:
        提取到的 ``message_id``;无法提取时返回空字符串。
    """
    if not isinstance(payload, dict):
        return ""
    mid = payload.get("message_id", "")
    if isinstance(mid, str) and mid:
        return mid
    metadata = payload.get("metadata", {})
    if isinstance(metadata, dict):
        mid = metadata.get("message_id", "")
        return mid if isinstance(mid, str) else ""
    return ""
