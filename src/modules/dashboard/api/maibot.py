"""
M aiBot 控制 API

提供 MaiBot 插件控制 Amaidesu 动作和情绪的接口。
"""

import time
import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, status

from src.modules.dashboard.schemas.maibot import MaibotActionRequest, MaibotActionResponse
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.decision import IntentPayload
from src.modules.logging import get_logger
from src.modules.types.intent import Intent, IntentMetadata


if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("MaibotAPI")


@router.post("/action", response_model=MaibotActionResponse)
async def handle_maibot_action(
    request: MaibotActionRequest,
    server: "DashboardServer",
) -> MaibotActionResponse:
    event_bus = server.event_bus
    if not event_bus:
        return MaibotActionResponse(success=False, error="Event bus not available")

    try:
        emotion = request.emotion
        speech = request.text

        intent = Intent(
            emotion=emotion,
            action=request.action,
            speech=speech,
            context="来源: maibot_api",
            metadata=IntentMetadata(
                source_id="maibot_api",
                decision_time=int(time.time() * 1000),
                parser_type="maibot_api",
                extra={"priority": request.priority, "action_params": request.action_params},
            ),
        )

        payload = IntentPayload.from_intent(intent, provider="maibot_api")

        await event_bus.emit(
            CoreEvents.DECISION_INTENT_GENERATED,
            payload,
            source="dashboard.maibot",
        )

        intent_id = str(uuid.uuid4())
        logger.info(f"处理 MaiBot 动作成功: {intent_id}")

        return MaibotActionResponse(success=True, intent_id=intent_id)

    except Exception as e:
        logger.error(f"处理 MaiBot 动作失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from None
