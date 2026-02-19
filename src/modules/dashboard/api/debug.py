"""
调试 API

提供调试和测试接口。
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends

from src.modules.context.models import MessageRole
from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.debug import (
    EventBusStatsResponse,
    InjectIntentRequest,
    InjectIntentResponse,
    InjectMessageRequest,
    InjectMessageResponse,
)
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.decision import IntentPayload
from src.modules.events.payloads.input import MessageReadyPayload
from src.modules.logging import get_logger
from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.types.intent import Intent, IntentAction

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
    """注入测试消息到系统"""
    event_bus = server.event_bus
    if not event_bus:
        return InjectMessageResponse(success=False, error="Event bus not available")

    try:
        # 构造 NormalizedMessage
        message = NormalizedMessage(
            text=request.text,
            source=request.source,
            data_type=request.data_type,
            importance=request.importance,
            timestamp=datetime.now().timestamp(),
        )

        # 通过 EventBus 发布事件
        # 使用 MessageReadyPayload.from_normalized_message 构造 Payload
        payload = MessageReadyPayload.from_normalized_message(message)

        # 使用 CoreEvents.INPUT_MESSAGE_READY 常量（不要硬编码字符串）
        await event_bus.emit(
            CoreEvents.INPUT_MESSAGE_READY,
            payload,
            source="dashboard.debug",
        )

        # 存储到 ContextService
        context_service = server.context_service
        if context_service:
            try:
                await context_service.add_message(
                    session_id=request.source,
                    role=MessageRole.USER,
                    content=request.text,
                    metadata={"importance": request.importance, "data_type": request.data_type},
                )
            except Exception as e:
                logger.warning(f"存储消息到 ContextService 失败: {e}")

        message_id = str(uuid.uuid4())
        logger.info(f"注入消息成功: {message_id}")

        return InjectMessageResponse(success=True, message_id=message_id)

    except Exception as e:
        logger.error(f"注入消息失败: {e}")
        return InjectMessageResponse(success=False, error=str(e))


@router.post("/inject-intent", response_model=InjectIntentResponse)
async def inject_intent(
    request: InjectIntentRequest,
    server: ServerDep,
) -> InjectIntentResponse:
    """注入测试 Intent 到系统"""
    event_bus = server.event_bus
    if not event_bus:
        return InjectIntentResponse(success=False, error="Event bus not available")

    try:
        # 构造 Intent 对象
        intent = Intent(
            original_text=request.text,
            response_text=request.response_text or request.text,
            emotion=request.emotion,
            actions=[
                IntentAction(
                    type=a.get("type", "expression"),
                    params=a.get("params", {}),
                    priority=a.get("priority", 50),
                )
                for a in request.actions
            ],
            metadata={},
            timestamp=datetime.now().timestamp(),
        )

        # 通过 EventBus 发布事件
        payload = IntentPayload.from_intent(intent, provider="dashboard_debug")

        await event_bus.emit(
            CoreEvents.DECISION_INTENT_GENERATED,
            payload,
            source="dashboard.debug",
        )

        # 存储到 ContextService（使用 source 作为 session_id）
        context_service = server.context_service
        if context_service:
            try:
                await context_service.add_message(
                    session_id=request.source,
                    role=MessageRole.ASSISTANT,
                    content=intent.response_text,
                    metadata={"emotion": request.emotion, "original_text": request.text},
                )
            except Exception as e:
                logger.warning(f"存储 Intent 到 ContextService 失败: {e}")

        intent_id = str(uuid.uuid4())
        logger.info(f"注入 Intent 成功: {intent_id}")

        return InjectIntentResponse(success=True, intent_id=intent_id)

    except Exception as e:
        logger.error(f"注入 Intent 失败: {e}")
        return InjectIntentResponse(success=False, error=str(e))


@router.get("/event-bus/stats", response_model=EventBusStatsResponse)
async def get_event_bus_stats(
    server: ServerDep,
) -> EventBusStatsResponse:
    """获取 EventBus 统计"""
    event_bus = server.event_bus
    if not event_bus:
        return EventBusStatsResponse()

    try:
        # 获取所有统计数据
        all_stats = event_bus.get_all_stats() if hasattr(event_bus, "get_all_stats") else {}

        # 计算总事件数和订阅者数
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
