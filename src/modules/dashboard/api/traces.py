"""
Trace 聚合 API（Wave 8 简化 + Wave U1 / B6 重写）

从 EventHistoryService 中按 message_id 聚合三段链路：
- messages: room.message.* 事件（采集器行为流）
- planning: planner.checkpoint / agenda.update 事件（决策与编排）
- execution: tool.result.* 事件（工具调用结果回传）

不依赖额外存储，纯查询 EventHistoryService 内存环形缓冲。
注意：事件类型字符串必须与 EventHistoryRecorder 中写入 EventRecord.type
的字面量保持一致。

----------------------------------------------------------------------
Wave U1 / B6 已知 linkage 缺失（诚实实现，不伪造数据）：

下列 Payload 字段在 v2 中均未携带 ``message_id``（已查 src/modules/events/payloads/
内 room.py / planner.py / agenda.py / tool_result.py 验证）：

- ``RoomMessagePayload``（room.message.*）—— 无 ``message_id``（仅有
  ``live_session_id`` / ``user.id``，二者均非单条消息唯一标识）。
- ``AgendaPayload`` / ``CheckpointPayload``（agenda.update / planner.checkpoint）——
  无 ``message_id``（决策与编排上下文不依赖单条消息）。
- ``ToolResultPayload``（tool.result.*）—— 无 ``message_id``（异步工具回传
  仅含 ``tool_name`` / ``status`` / ``result``，与触发消息无显式关联）。

因此当前实现只能"messages 段全量返回 + planning/execution 段空数组"。
待 EventRecord.data 中补齐 message_id 后，本模块只需修改聚合过滤逻辑即可
启用完整三段对齐。后续工作的关键字段命名约定（待 §1.46 事件契约扩展）
建议为 ``message_id``，与 NormalizedMessage.message_id 同源。
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

# EventRecord.data 字典下承载业务负载的键名
_KEY_MESSAGE = "message"

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
        见模块顶部"Wave U1 / B6 已知 linkage 缺失"注释——当前 messages 段
        返回 room.message 全量记录，planning/execution 段返回空数组（不伪造）。
        后续 message_id 字段补齐后，仅需修改 ``_collect_segments`` 过滤逻辑。
    """
    msg_event = _find_room_message_event(history, message_id)
    if not msg_event:
        return None

    msg_data = msg_event.data.get(_KEY_MESSAGE, {})
    if not isinstance(msg_data, dict):
        msg_data = {}

    segments = _collect_segments(history, message_id)

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
        "segments": segments,
    }

    return trace


def _collect_segments(
    history: EventHistoryService,
    message_id: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """按 message_id 聚合三段记录。

    当前实现（Wave U1 / B6）受模块顶部注释描述的 linkage 缺失约束：
    - messages 段：返回 room.message 全量记录（data.message_id 不存在，
      无法按 message_id 过滤；保留完整行为流由前端按时间窗裁剪）。
    - planning 段：返回 []（AgendaPayload/CheckpointPayload 不携带 message_id）。
    - execution 段：返回 []（ToolResultPayload 不携带 message_id）。

    EventRecord.data 补齐 message_id 后，仅替换本函数三个 return 行即可启用
    三段对齐，无需修改 _build_trace 调用方。``message_id`` 参数保留是为
    对齐未来签名；当前未参与过滤。
    """
    records = _iter_recent(history)

    messages: List[Dict[str, Any]] = []
    for record in records:
        if record.type != ROOM_MESSAGE_TYPE:
            continue
        messages.append(_serialize_segment_record(record))

    return {
        "messages": messages,
        "planning": [],
        "execution": [],
    }


def _serialize_segment_record(record: EventRecord) -> Dict[str, Any]:
    """把 EventRecord 序列化为前端消费的字典（轻量拷贝，避免泄露内部对象）。"""
    return {
        "id": record.id,
        "type": record.type,
        "timestamp": record.timestamp,
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
        candidate_id = _extract_message_id(record.data.get(_KEY_MESSAGE, {}))
        if candidate_id == message_id:
            return record
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
