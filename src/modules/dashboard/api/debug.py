"""
调试 API

提供调试和测试接口。
"""

import uuid
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException

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
    """注入测试消息到系统（发布 room.message.danmaku，走真实弹幕链路）。

    会话语义：弹幕经 StorageLedger 落 live_chat（单一事实源），主播
    Agent 决策/表达历史直接读 live_chat——注入消息天然进入决策上下文。
    """
    event_bus = server.event_bus
    if not event_bus:
        return InjectMessageResponse(success=False, error="Event bus not available")

    try:
        # 通过 EventBus 发布 room.message.danmaku（v2 语义域事件）。
        # user.name 用 source 承载昵称——前端注入的"来源标识"在直播间语境
        # 就是观众昵称，Agent 侧统一读 user_nickname。
        # 场次归属（live_session_id）由场次盖章拦截器统一注入；message_id
        # 现场生成，与响应回传同一 ID（可对账"注入 → 决策 → 回复"链路）。
        message_id = str(uuid.uuid4())
        payload = RoomMessagePayload(
            message_id=message_id,
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

        logger.info(f"注入消息成功: {message_id}")
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
