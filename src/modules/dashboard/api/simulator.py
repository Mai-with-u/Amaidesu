"""
模拟直播间 Dashboard API

提供模拟直播间的控制面板后端接口：
- 状态查询（status/stats）
- 启停控制（start/stop）
- 运行时参数更新（params）
- 手动触发（gift_rain/topic_injection）
- Token 预算重置
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("SimulatorAPI")

ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]

# --- Schemas ---


class SimulatorStatus(BaseModel):
    is_running: bool = False
    current_state: str = "STOPPED"
    started_at_ms: int = 0
    config_snapshot: Dict[str, Any] = Field(default_factory=dict)
    is_collector_available: bool = False


class SimulatorStats(BaseModel):
    total_messages: int = 0
    total_tokens: int = 0
    messages_by_type: Dict[str, int] = Field(default_factory=dict)
    messages_by_role: Dict[str, int] = Field(default_factory=dict)


class SimulatorParams(BaseModel):
    base_rate_per_minute: Optional[float] = None
    burst_multiplier: Optional[float] = None
    temp_passerby_ratio: Optional[float] = None
    gift_probability: Optional[float] = None
    cadence_mode: Optional[str] = None
    fixed_interval_s: Optional[float] = None


class TriggerPayload(BaseModel):
    duration_s: int = 30
    topic: str = ""
    persona_name: str = ""


# --- Helper ---


def _get_collector(server: DashboardServer) -> Any:
    """从 InputManager 获取 SimulatedLiveStreamCollector 实例"""
    if not server.input_manager:
        return None
    for c in server.input_manager.get_collectors():
        if c.__class__.__name__ == "SimulatedLiveStreamCollector":
            return c
    return None


# --- Endpoints ---


@router.get("/simulator/status")
async def get_status(server: ServerDep) -> SimulatorStatus:
    """获取模拟器状态"""
    collector = _get_collector(server)
    if collector is None:
        return SimulatorStatus(is_collector_available=False)
    return SimulatorStatus(
        is_running=getattr(collector, "is_started", False),
        current_state=getattr(collector, "is_started", False) and "running" or "stopped",
        config_snapshot=collector.typed_config.model_dump() if hasattr(collector, "typed_config") else {},
        is_collector_available=True,
    )


@router.get("/simulator/stats")
async def get_stats(server: ServerDep) -> SimulatorStats:
    """获取模拟器统计"""
    collector = _get_collector(server)
    if collector is None:
        return SimulatorStats()
    return SimulatorStats(
        total_messages=getattr(collector, "_total_messages", 0),
        total_tokens=getattr(collector, "_token_budget", None) and collector._token_budget.get_usage_last_hour() or 0,
        messages_by_type=getattr(collector, "_messages_by_type", {}),
        messages_by_role=collector._persona_pool.get_stats() if collector._persona_pool else {},
    )


@router.get("/simulator/personas")
async def get_personas(server: ServerDep) -> list:
    """返回常驻人设列表"""
    collector = _get_collector(server)
    if collector is None or collector._persona_pool is None:
        return []
    return [
        {
            "user_id": p.user_id,
            "user_nickname": p.user_nickname,
            "role": p.role.value,
            "fans_medal_level": p.fans_medal_level,
            "guard_level": p.guard_level,
            "messages_generated": p.messages_generated,
        }
        for p in collector._persona_pool.list_residents()
    ]


@router.post("/simulator/start")
async def start_simulator(server: ServerDep) -> Dict[str, Any]:
    """启动模拟器"""
    collector = _get_collector(server)
    if collector is None:
        raise HTTPException(status_code=404, detail="模拟器 Collector 未加载")
    if getattr(collector, "is_started", False):
        return {"status": "already_running"}
    await collector.start()
    return {"status": "started"}


@router.post("/simulator/stop")
async def stop_simulator(server: ServerDep) -> Dict[str, Any]:
    """停止模拟器"""
    collector = _get_collector(server)
    if collector is None:
        raise HTTPException(status_code=404, detail="模拟器 Collector 未加载")
    if not getattr(collector, "is_started", False):
        return {"status": "already_stopped"}
    await collector.stop()
    return {"status": "stopped"}


@router.post("/simulator/params")
async def update_params(params: SimulatorParams, server: ServerDep) -> Dict[str, Any]:
    """更新运行时参数"""
    collector = _get_collector(server)
    if collector is None:
        raise HTTPException(status_code=404, detail="模拟器 Collector 未加载")
    update_dict = params.model_dump(exclude_none=True)
    if update_dict:
        collector.update_runtime_config(**update_dict)
    return {"status": "updated", "params": update_dict}


@router.post("/simulator/trigger/gift_rain")
async def trigger_gift_rain(payload: TriggerPayload, server: ServerDep) -> Dict[str, Any]:
    """手动触发礼物雨"""
    collector = _get_collector(server)
    if collector is None:
        raise HTTPException(status_code=404, detail="模拟器 Collector 未加载")
    collector.trigger_gift_rain(payload.duration_s)
    return {"status": "gift_rain_triggered", "duration_s": payload.duration_s}


@router.post("/simulator/trigger/topic_injection")
async def trigger_topic_injection(payload: TriggerPayload, server: ServerDep) -> Dict[str, Any]:
    """注入话题到上下文"""
    collector = _get_collector(server)
    if collector is None:
        raise HTTPException(status_code=404, detail="模拟器 Collector 未加载")
    collector.trigger_topic_injection(payload.topic)
    return {"status": "topic_injected", "topic": payload.topic}


@router.post("/simulator/reset_token_budget")
async def reset_token_budget(server: ServerDep) -> Dict[str, Any]:
    """重置 Token 预算"""
    collector = _get_collector(server)
    if collector is None:
        raise HTTPException(status_code=404, detail="模拟器 Collector 未加载")
    collector.reset_token_budget()
    return {"status": "budget_reset"}


# --- WebSocket ---


@router.websocket("/ws/simulator/stream")
async def ws_simulator_stream(websocket: WebSocket):
    """WebSocket 实时推送模拟器生成的消息"""
    await websocket.accept()
    # 尝试从 server 获取 collector
    try:
        server = get_dashboard_server()
    except Exception:
        await websocket.close(code=1011)
        return
    collector = _get_collector(server)
    if collector is None:
        await websocket.send_json({"error": "模拟器 Collector 未加载"})
        await websocket.close(code=1011)
        return

    queue = collector.subscribe_for_ws()
    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_json(
                    {
                        "type": msg.data_type,
                        "text": msg.text,
                        "user_id": msg.user_id,
                        "user_nickname": msg.user_nickname,
                        "source": msg.source,
                        "importance": msg.importance,
                    }
                )
            except asyncio.TimeoutError:
                # 心跳 ping
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        collector.unsubscribe_ws(queue)
