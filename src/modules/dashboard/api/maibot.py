"""
MaiBot 控制 API

提供 MaiBot 插件控制 Amaidesu 动作和情绪的接口。
"""

import uuid
from typing import TYPE_CHECKING, Annotated, List

from fastapi import APIRouter, Depends, HTTPException, status

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.maibot import MaibotActionRequest, MaibotActionResponse
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.decision import IntentPayload
from src.modules.logging import get_logger
from src.modules.types.intent import (
    DecisionMetadata,
    EmotionType,
    Intent,
    IntentAction,
    ParserType,
    SourceContext,
)

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("MaibotAPI")

ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


@router.post("/action", response_model=MaibotActionResponse)
async def handle_maibot_action(
    request: MaibotActionRequest,
    server: ServerDep,
) -> MaibotActionResponse:
    """处理 MaiBot 动作/情绪控制请求"""

    event_bus = server.event_bus
    if not event_bus:
        logger.error("Event bus not available")
        return MaibotActionResponse(
            success=False,
            error="Event bus not available",
        )

    # 验证请求参数（至少需要 action 或 emotion 之一）
    if not request.action and not request.emotion:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="至少需要提供 action 或 emotion 参数",
        )

    try:
        # 构造 Intent 对象
        intent = Intent(
            original_text=request.text or "",
            response_text=request.text or "",
            emotion=request.emotion or EmotionType.NEUTRAL,
            actions=_build_actions(request),
            source_context=SourceContext(
                source="maibot",
                data_type="control",
                importance=0.8,
            ),
            decision_metadata=DecisionMetadata(
                parser_type=ParserType.REPLAY,
                extra={"maibot_control": True},
            ),
        )

        # 通过 EventBus 发布 decision.intent.generated 事件
        payload = IntentPayload.from_intent(intent, provider="maibot_api")
        await event_bus.emit(
            CoreEvents.DECISION_INTENT_GENERATED,
            payload,
            source="maibot",
        )

        intent_id = str(uuid.uuid4())
        logger.info(
            f"MaiBot 控制请求处理成功: intent_id={intent_id}, action={request.action}, emotion={request.emotion}"
        )

        return MaibotActionResponse(
            success=True,
            intent_id=intent_id,
            message="Intent created and emitted successfully",
        )

    except Exception as e:
        logger.error(f"MaiBot 控制请求处理失败: {e}")
        return MaibotActionResponse(
            success=False,
            error=str(e),
        )


def _build_actions(request: MaibotActionRequest) -> List[IntentAction]:
    """构建动作列表"""
    if not request.action:
        return []

    return [
        IntentAction(
            type=request.action,
            params=request.action_params,
            priority=request.priority,
        )
    ]
