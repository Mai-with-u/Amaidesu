"""
MaiBot 控制 API

提供 MaiBot 插件控制 Amaidesu 动作和情绪的接口。
"""

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.maibot import MaibotActionRequest, MaibotActionResponse
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.decision import IntentPayload
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.types.intent import (
    Intent,
    IntentAction,
    IntentEmotion,
    IntentMetadata,
)

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("MaibotAPI")


@router.post("/action", response_model=MaibotActionResponse)
async def handle_maibot_action(
    request: MaibotActionRequest,
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
) -> MaibotActionResponse:
    """构造结构化 Intent,发布到 EventBus,返回 UUID4 intent_id。

    - 校验 action.name 必须含 '.' (handler-qualified),否则 422
    - 仅 text(无 action 无 emotion)也接受
    """
    event_bus = server.event_bus
    if not event_bus:
        return MaibotActionResponse(success=False, error="Event bus not available")

    # action 必须 handler-qualified(包含 '.')
    if request.action is not None and "." not in request.action.name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"action.name '{request.action.name}' 不是 handler-qualified 格式"
                f"(需要形如 'warudo.wave',带 '<handler>.<action>' 前缀)"
            ),
        )

    try:
        emotion_obj: IntentEmotion | None = None
        if request.emotion is not None:
            try:
                emotion_obj = IntentEmotion(
                    name=request.emotion.name,
                    intensity=request.emotion.intensity,
                )
            except ValidationError as e:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"emotion 校验失败: {e.errors()}",
                ) from None

        action_obj: IntentAction | None = None
        if request.action is not None:
            action_obj = IntentAction(
                name=request.action.name,
                parameters=request.action.parameters,
            )

        intent = Intent(
            speech=request.text,
            metadata=IntentMetadata(
                source_id="maibot_api",
                decision_time_ms=now_ms(),
            ),
            emotion=emotion_obj,
            action=action_obj,
        )

        payload = IntentPayload.from_intent(intent, name="maibot_api")

        await event_bus.emit(
            CoreEvents.DECISION_INTENT_GENERATED,
            payload,
            source="dashboard.maibot",
        )

        intent_id = str(uuid.uuid4())

        return MaibotActionResponse(success=True, intent_id=intent_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"处理 MaiBot 动作失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from None
