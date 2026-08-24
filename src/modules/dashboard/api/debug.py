"""
调试 API（Wave 6 重写）

提供调试和测试接口。

Wave 6 变更：
- 移除 ``/inject-intent`` 端点（Intent 已被 DISCARD，决策出口=工具调用）。
- ``/inject-message`` 端点改为发布 ``room.message.danmaku`` 事件（v2 语义域事件）。
- 移除对 ``payloads.decision.IntentPayload`` 和 ``payloads.input.MessageReadyPayload`` 的依赖。
"""

import uuid
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException

from src.modules.context.models import MessageRole
from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.debug import (
    EventBusStatsResponse,
    InjectMessageRequest,
    InjectMessageResponse,
)
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import RoomMessagePayload, RoomMessageUser
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.types.message_type import MessageTypeNotRegistered, require_message_type

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("DebugAPI")


# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


@router.post("/inject-message", response_model=InjectMessageResponse)
async def inject_message(
    request: InjectMessageRequest,
    server: ServerDep,
) -> InjectMessageResponse:
    """注入测试消息到系统（Wave 6：发布 room.message.danmaku）"""
    event_bus = server.event_bus
    if not event_bus:
        return InjectMessageResponse(success=False, error="Event bus not available")

    try:
        try:
            require_message_type(request.data_type)
        except MessageTypeNotRegistered as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        message = NormalizedMessage(
            text=request.text,
            source=request.source,
            data_type=request.data_type,
            importance=request.importance,
            timestamp=now_ms(),
        )

        # 通过 EventBus 发布 room.message.danmaku（v2 语义域事件）
        payload = RoomMessagePayload(
            live_session_id=request.source,
            message_type="danmaku",
            user=RoomMessageUser(
                id=request.source,
                name=request.source,
            ),
            content=request.text,
            timestamp_ms=now_ms(),
        )
        await event_bus.emit(
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            payload,
            source="dashboard.debug",
        )

        context_service = server.context_service
        if context_service:
            try:
                await context_service.add_message(
                    session_id=request.source,
                    role=MessageRole.USER,
                    content=request.text,
                )
            except Exception as e:
                logger.warning(f"存储消息到 ContextService 失败: {e}")

        message_id = str(uuid.uuid4())
        logger.info(f"注入消息成功: {message_id}")
        # suppress unused var
        _ = message
        return InjectMessageResponse(success=True, message_id=message_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"注入消息失败: {e}")
        return InjectMessageResponse(success=False, error=str(e))


@router.get("/event-bus/stats", response_model=EventBusStatsResponse)
async def get_event_bus_stats(
    server: ServerDep,
) -> EventBusStatsResponse:
    """获取 EventBus 统计"""
    event_bus = server.event_bus
    if not event_bus:
        return EventBusStatsResponse()

    try:
        all_stats = event_bus.get_all_stats() if hasattr(event_bus, "get_all_stats") else {}

        total_events = 0
        total_subscribers = 0
        events_by_name: dict[str, int] = {}

        for event_name, stats in all_stats.items():
            total_events += stats.emit_count
            total_subscribers += stats.listener_count
            events_by_name[event_name] = stats.emit_count

        return EventBusStatsResponse(
            total_events=total_events,
            total_subscribers=total_subscribers,
            events_by_name=events_by_name,
        )
    except Exception as e:
        logger.error(f"获取 EventBus 统计失败: {e}")
        return EventBusStatsResponse()
