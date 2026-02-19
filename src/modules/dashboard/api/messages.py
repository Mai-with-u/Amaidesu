"""
消息历史 API

提供消息历史的查询接口。
"""

from typing import TYPE_CHECKING, Annotated, Optional
import time

from fastapi import APIRouter, Depends, Query

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.message import (
    MessageItem,
    MessageListResponse,
    SessionInfo,
    SessionListResponse,
)
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("MessagesAPI")


# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    active_only: bool = Query(False, description="只返回活跃会话"),
    limit: int = Query(50, ge=1, le=200, description="返回数量限制"),
    server: ServerDep = ...,
) -> SessionListResponse:
    """获取会话列表"""
    context_service = server.context_service
    if not context_service:
        return SessionListResponse(sessions=[], total=0)

    sessions = []
    try:
        session_list = await context_service.list_sessions(active_only=active_only, limit=limit)

        for session in session_list:
            # 获取会话详细信息
            info = await context_service.get_session_info(session.session_id)

            # 计算是否活跃（1小时内有活动）
            is_active = (time.time() - session.last_active) < 3600

            sessions.append(
                SessionInfo(
                    session_id=session.session_id,
                    created_at=session.created_at,
                    last_active=session.last_active,
                    message_count=info.message_count if info else session.message_count,
                    is_active=is_active,
                )
            )
    except Exception as e:
        logger.error(f"获取会话列表失败: {e}")

    return SessionListResponse(sessions=sessions, total=len(sessions))


@router.get("/sessions/{session_id}/messages", response_model=MessageListResponse)
async def get_session_messages(
    session_id: str,
    limit: int = Query(20, ge=1, le=100, description="返回消息数"),
    before_timestamp: Optional[float] = Query(None, description="游标：返回此时间戳之前的消息"),
    server: ServerDep = ...,
) -> MessageListResponse:
    """获取会话消息（游标分页）"""
    context_service = server.context_service
    if not context_service:
        return MessageListResponse(messages=[], has_more=False, limit=limit)

    messages = []
    has_more = False
    next_cursor = None

    try:
        # 使用游标分页
        history = await context_service.get_history(
            session_id=session_id,
            limit=limit + 1,  # 多获取一条判断 has_more
            before_timestamp=before_timestamp,
        )

        if len(history) > limit:
            has_more = True
            history = history[:limit]

        for msg in history:
            messages.append(
                MessageItem(
                    id=msg.message_id,
                    session_id=session_id,
                    role=msg.role.value if hasattr(msg.role, "value") else str(msg.role),
                    content=msg.content,
                    timestamp=msg.timestamp,
                    metadata=msg.metadata,
                )
            )

        # 设置下一页游标
        if messages and has_more:
            next_cursor = messages[-1].timestamp

    except Exception as e:
        logger.error(f"获取消息历史失败: {e}")

    return MessageListResponse(
        messages=messages,
        has_more=has_more,
        next_cursor=next_cursor,
        limit=limit,
    )


@router.get("/messages", response_model=MessageListResponse)
async def list_messages(
    limit: int = Query(20, ge=1, le=100, description="返回消息数"),
    before_timestamp: Optional[float] = Query(None, description="游标"),
    server: ServerDep = ...,
) -> MessageListResponse:
    """获取最近消息（默认会话）"""
    # 获取默认会话或最近活跃会话
    context_service = server.context_service
    if not context_service:
        return MessageListResponse(messages=[], has_more=False, limit=limit)

    try:
        sessions = await context_service.list_sessions(active_only=True, limit=1)
        if not sessions:
            return MessageListResponse(messages=[], has_more=False, limit=limit)

        return await get_session_messages(
            session_id=sessions[0].session_id,
            limit=limit,
            before_timestamp=before_timestamp,
            server=server,
        )
    except Exception as e:
        logger.error(f"获取最近消息失败: {e}")
        return MessageListResponse(messages=[], has_more=False, limit=limit)
