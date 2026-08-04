"""
主动发言 Dashboard API

提供 WebUI 触发端点，把外部"主动开口"指令转发到 Decision 阶段：

- ``POST /api/v1/proactive/speak`` → ``DeciderManager.trigger_proactive(topic_hint)``
- 返回实际触发成功的 Decider 名称列表，供前端确认"谁响应了这次请求"
- ``topic_hint`` 可选（用于给 LLM 提供话题线索），最长 200 字符

调用语义（参见 :mod:`src.stages.decision.manager`）：
- manager 仅向"实现了 ``trigger_proactive`` 方法"的 Decider 转发（鸭子类型跳过）
- 失败的 Decider 被隔离（异常吞掉 + 日志），不会影响其他 Decider
- 返回列表只含"成功调用"的 Decider 名，失败的不泄漏给 API 响应
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("ProactiveAPI")

ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


# --- Schemas ---


class ProactiveSpeakRequest(BaseModel):
    """主动发言触发请求体"""

    topic_hint: Optional[str] = Field(
        default=None,
        max_length=200,
        description="话题线索（可选），透传给各 Decider 用于 prompt 注入；最长 200 字符",
    )


# --- Endpoints ---


@router.post("/proactive/speak")
async def trigger_proactive_speak(
    payload: ProactiveSpeakRequest,
    server: ServerDep,
) -> Dict[str, Any]:
    """触发一次主动发言

    调用 ``DeciderManager.trigger_proactive(topic_hint)`` 把请求转发到所有
    实现了 ``trigger_proactive`` 接口的 Decider。返回 ``status="queued"``
    以及实际触发的 Decider 名称列表，前端可用于提示"已通知哪些决策器"。
    """
    if server.decision_manager is None:
        raise HTTPException(status_code=404, detail="决策器未加载")

    # ``trigger_proactive`` 由 ``DeciderManager`` 额外提供，未在 ``ManagerStatusProvider``
    # 协议声明，故需 type: ignore[attr-defined]。组合根注入的是 ``DeciderManager`` 实例。
    triggered = await server.decision_manager.trigger_proactive(payload.topic_hint)  # type: ignore[attr-defined]
    return {
        "status": "queued",
        "deciders": triggered,
        "topic_hint": payload.topic_hint,
    }
