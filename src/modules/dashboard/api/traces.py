"""
Trace 聚合 API

从 EventHistoryService 中查询并聚合消息全链路追踪数据。

链路构成:
- Input 阶段:  EventRecord(type="message.received",    data.message.message_id)
- Decision 阶段: EventRecord(type="decision.intent",    data.intent_data.metadata.source_message_id)
- Output 阶段:   EventRecord(type="output.render",      data.intent_data.metadata.source_message_id)

不依赖额外存储,纯查询 EventHistoryService 内存环形缓冲。
注意: 事件类型字符串必须与 EventHistoryRecorder 中写入 EventRecord.type 的字面量保持一致。
"""

from typing import TYPE_CHECKING, Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.events.event_history import EventHistoryService, EventRecord
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("TracesAPI")

# 与 EventHistoryRecorder 写入 EventRecord.type 的字面量保持一致
_TYPE_MESSAGE_RECEIVED = "message.received"
_TYPE_DECISION_INTENT = "decision.intent"
_TYPE_OUTPUT_RENDER = "output.render"

# EventRecord.data 字典下承载业务负载的键名
_KEY_MESSAGE = "message"
_KEY_INTENT_DATA = "intent_data"

ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


@router.get("/traces")
async def list_traces(
    limit: int = Query(20, ge=1, le=100, description="返回最大 Trace 数"),
    server: ServerDep = ...,
):
    """获取最近的 Trace 列表。

    策略: 从最近的 ``message.received`` 事件中提取 ``message_id``,
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

    received = history.query(types=[_TYPE_MESSAGE_RECEIVED], limit=limit)

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

    搜索策略:
    - message.received:   data.message.message_id == message_id
    - decision.intent:    data.intent_data.metadata.source_message_id == message_id
    - output.render:      data.intent_data.metadata.source_message_id == message_id

    Args:
        history: EventHistoryService 实例。
        message_id: 目标 NormalizedMessage 的 ID。

    Returns:
        聚合的 Trace 字典;若 message.received 事件未找到则返回 ``None``。
    """
    # 1) 找到消息事件(必须有,否则视为无此链路)
    msg_event = _find_event(history, _TYPE_MESSAGE_RECEIVED, message_id, _KEY_MESSAGE)
    if not msg_event:
        return None

    msg_data = msg_event.data.get(_KEY_MESSAGE, {})
    if not isinstance(msg_data, dict):
        msg_data = {}

    # 2) 找到对应的决策事件(可能没有,例如 CommandDecider 不总是输出)
    decision_event = _find_event(history, _TYPE_DECISION_INTENT, message_id, _KEY_INTENT_DATA)

    # 3) 找到对应的输出事件(可能有多个 Handler)
    output_events = _find_all_events(history, _TYPE_OUTPUT_RENDER, message_id, _KEY_INTENT_DATA)

    # 构建 Trace
    trace: Dict[str, Any] = {
        "message_id": message_id,
        "message": {
            "text": msg_data.get("text", ""),
            "source": msg_data.get("source", ""),
            "data_type": msg_data.get("data_type", ""),
            "timestamp_ms": msg_data.get("timestamp_ms", 0),
            "user_id": msg_data.get("user_id"),
            "user_nickname": msg_data.get("user_nickname"),
        },
        "decision": None,
        "outputs": [],
    }

    if decision_event:
        intent_data = decision_event.data.get(_KEY_INTENT_DATA, {})
        if not isinstance(intent_data, dict):
            intent_data = {}
        trace["decision"] = {
            "decider": decision_event.data.get("name", ""),
            "speech": intent_data.get("speech", ""),
            "emotion": intent_data.get("emotion"),
            "action": intent_data.get("action"),
            "timestamp": decision_event.timestamp,
            "elapsed_ms": _calc_elapsed_ms(msg_event.timestamp, decision_event.timestamp),
        }

    for oe in output_events:
        intent_data = oe.data.get(_KEY_INTENT_DATA, {})
        if not isinstance(intent_data, dict):
            intent_data = {}
        trace["outputs"].append(
            {
                "handler": oe.data.get("name", ""),
                "speech": intent_data.get("speech", ""),
                "action": intent_data.get("action"),
                "timestamp": oe.timestamp,
                "elapsed_ms": _calc_elapsed_ms(msg_event.timestamp, oe.timestamp),
            }
        )

    # 计算总耗时(最早事件到最晚事件)
    all_ts: List[float] = [msg_event.timestamp]
    if decision_event:
        all_ts.append(decision_event.timestamp)
    for oe in output_events:
        all_ts.append(oe.timestamp)
    trace["total_elapsed_ms"] = _calc_elapsed_ms(min(all_ts), max(all_ts)) if len(all_ts) > 1 else 0

    return trace


def _find_event(
    history: EventHistoryService,
    event_type: str,
    message_id: str,
    data_key: str,
) -> Optional[EventRecord]:
    """在 EventHistoryService 中查找匹配 ``message_id`` 的首条事件。

    直接遍历环形缓冲(最大 5000 条,管理页面足够快),
    因为需要在 payload 内嵌套字段(如 ``intent_data.metadata.source_message_id``)上过滤,
    现有 ``query()`` 接口不支持。

    Args:
        history: EventHistoryService 实例。
        event_type: 要匹配的事件类型(如 ``message.received``)。
        message_id: 目标 ``message_id``。
        data_key: EventRecord.data 字典中承载业务负载的键名。

    Returns:
        首条匹配的事件;未找到则返回 ``None``。
    """
    for event in history.get_recent(history.max_events):
        if event.type != event_type:
            continue
        candidate_id = _extract_message_id(event, event_type, data_key)
        if candidate_id == message_id:
            return event
    return None


def _find_all_events(
    history: EventHistoryService,
    event_type: str,
    message_id: str,
    data_key: str,
) -> List[EventRecord]:
    """查找所有匹配 ``message_id`` 的事件(可能多个 ``output.render``)。"""
    results: List[EventRecord] = []
    for event in history.get_recent(history.max_events):
        if event.type != event_type:
            continue
        candidate_id = _extract_message_id(event, event_type, data_key)
        if candidate_id == message_id:
            results.append(event)
    return results


def _extract_message_id_from_received(event: EventRecord) -> str:
    """从 ``message.received`` 事件中提取 ``message_id``。

    优先从 ``data.message.message_id`` 获取;若不存在则尝试 ``data.metadata.message_id``。
    """
    message = event.data.get(_KEY_MESSAGE, {})
    if isinstance(message, dict):
        mid = message.get("message_id", "")
        if isinstance(mid, str) and mid:
            return mid
    metadata = event.data.get("metadata", {})
    if isinstance(metadata, dict):
        mid = metadata.get("message_id", "")
        if isinstance(mid, str):
            return mid
    return ""


def _extract_message_id(event: EventRecord, event_type: str, data_key: str) -> str:
    """根据事件类型从 ``event.data[data_key]`` 中提取 ``message_id``。

    提取规则:
    - ``message.received``: ``payload.message_id``
    - ``decision.intent`` / ``output.render``: ``payload.metadata.source_message_id``

    Args:
        event: 事件记录。
        event_type: 事件类型。
        data_key: EventRecord.data 字典中承载业务负载的键名。

    Returns:
        提取到的 ``message_id``;无法提取时返回空字符串。
    """
    payload = event.data.get(data_key, {})
    if not isinstance(payload, dict):
        return ""

    if event_type == _TYPE_MESSAGE_RECEIVED:
        mid = payload.get("message_id", "")
        return mid if isinstance(mid, str) else ""

    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        return ""
    mid = metadata.get("source_message_id", "")
    return mid if isinstance(mid, str) else ""


def _calc_elapsed_ms(start: float, end: float) -> int:
    """计算两个时间戳之间的毫秒数。

    EventRecord.timestamp 是 Unix 秒(float)。

    Args:
        start: 起始时间戳(秒)。
        end: 结束时间戳(秒)。

    Returns:
        毫秒数差值(整数,负数保留以表达乱序事件)。
    """
    return int((end - start) * 1000)
