"""模拟直播间 Dashboard API

提供模拟直播间的控制面板后端接口：
- 状态查询（status/stats）
- 启停控制（start/stop）
- 运行时参数更新（params）
- 手动触发（gift_rain/topic_injection）
- Token 预算重置

重构后通过 ``server.simulator_service`` 获取模拟器实例，
不再依赖 ``input_manager.get_collectors()`` + 类名反查。
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
    from src.modules.simulator.service import SimulatorService
    from src.modules.simulator.simulator import LiveStreamSimulator

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


class PersonaGenerateRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=20, description="生成人设数量（1-20）")
    roles: Optional[list[str]] = Field(default=None, description="允许的角色列表；缺省为全部非路人角色")


class PersonaUpdateRequest(BaseModel):
    user_nickname: Optional[str] = None
    role: Optional[str] = None
    personality: Optional[str] = None
    speaking_style: Optional[str] = None
    fans_medal_level: Optional[int] = Field(default=None, ge=0, le=40)
    guard_level: Optional[int] = Field(default=None, ge=0, le=3)


# --- Helper ---


def _get_simulator_service(server: "DashboardServer") -> Optional["SimulatorService"]:
    """从 DashboardServer 获取 SimulatorService 实例"""
    return server.simulator_service


def _get_simulator(server: "DashboardServer") -> Optional["LiveStreamSimulator"]:
    """从 SimulatorService 获取模拟器实例"""
    service = _get_simulator_service(server)
    if service is None:
        return None
    return service.simulator


def _persona_to_dict(persona: Any) -> Dict[str, Any]:
    """Persona → 前端字典（含编辑所需完整字段）"""
    return {
        "user_id": persona.user_id,
        "user_nickname": persona.user_nickname,
        "role": persona.role.value,
        "personality": persona.personality,
        "speaking_style": persona.speaking_style,
        "fans_medal_level": persona.fans_medal_level,
        "guard_level": persona.guard_level,
        "messages_generated": persona.messages_generated,
    }


# --- Endpoints ---


@router.get("/simulator/status")
async def get_status(server: ServerDep) -> SimulatorStatus:
    """获取模拟器状态"""
    service = _get_simulator_service(server)
    if service is None:
        return SimulatorStatus(is_collector_available=False)

    sim = service.simulator
    is_running = service.is_running
    return SimulatorStatus(
        is_running=is_running,
        current_state="running" if is_running else "stopped",
        config_snapshot=sim.typed_config.model_dump() if sim is not None else {},
        is_collector_available=sim is not None,
    )


@router.get("/simulator/stats")
async def get_stats(server: ServerDep) -> SimulatorStats:
    """获取模拟器统计"""
    sim = _get_simulator(server)
    if sim is None:
        return SimulatorStats()
    return SimulatorStats(
        total_messages=getattr(sim, "_total_messages", 0),
        total_tokens=getattr(sim, "_token_budget", None) and sim._token_budget.get_usage_last_hour() or 0,
        messages_by_type=getattr(sim, "_messages_by_type", {}),
        messages_by_role=sim._persona_pool.get_stats() if sim._persona_pool else {},
    )


@router.get("/simulator/personas")
async def get_personas(server: ServerDep) -> list[Dict[str, Any]]:
    """返回常驻人设列表"""
    sim = _get_simulator(server)
    if sim is None or sim._persona_pool is None:
        return []
    return [_persona_to_dict(p) for p in sim._persona_pool.list_residents()]


@router.post("/simulator/personas/generate")
async def generate_personas(payload: PersonaGenerateRequest, server: ServerDep) -> Dict[str, Any]:
    """LLM 批量生成常驻人设并持久化"""
    sim = _get_simulator(server)
    if sim is None or sim._persona_pool is None:
        raise HTTPException(status_code=404, detail="模拟器服务未加载")
    wrapper = getattr(sim, "_llm_wrapper", None)
    if wrapper is None:
        raise HTTPException(status_code=503, detail="模拟器 LLM 服务不可用")

    existing = [p.user_nickname for p in sim._persona_pool.list_residents()]
    personas = await wrapper.generate_personas(
        count=payload.count,
        roles=payload.roles,
        existing_nicknames=existing,
    )
    if not personas:
        raise HTTPException(status_code=502, detail="人设生成失败（LLM 无有效输出）")
    added = sim._persona_pool.add_personas(personas)
    return {
        "status": "generated",
        "personas": [_persona_to_dict(p) for p in personas],
        "added": added,
        "skipped": len(personas) - added,
    }


@router.put("/simulator/personas/{user_id}")
async def update_persona(user_id: str, payload: PersonaUpdateRequest, server: ServerDep) -> Dict[str, Any]:
    """更新常驻人设"""
    sim = _get_simulator(server)
    if sim is None or sim._persona_pool is None:
        raise HTTPException(status_code=404, detail="模拟器服务未加载")
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=422, detail="没有可更新的字段")
    if not sim._persona_pool.update_persona(user_id, fields):
        raise HTTPException(status_code=404, detail=f"人设不存在: {user_id}")
    return {"status": "updated"}


@router.delete("/simulator/personas/{user_id}")
async def delete_persona(user_id: str, server: ServerDep) -> Dict[str, Any]:
    """删除常驻人设"""
    sim = _get_simulator(server)
    if sim is None or sim._persona_pool is None:
        raise HTTPException(status_code=404, detail="模拟器服务未加载")
    if not sim._persona_pool.delete_persona(user_id):
        raise HTTPException(status_code=404, detail=f"人设不存在: {user_id}")
    return {"status": "deleted"}


@router.post("/simulator/start")
async def start_simulator(server: ServerDep) -> Dict[str, Any]:
    """启动模拟器"""
    service = _get_simulator_service(server)
    if service is None:
        raise HTTPException(status_code=404, detail="模拟器服务未加载")
    if service.is_running:
        return {"status": "already_running"}
    await service.start()
    return {"status": "started"}


@router.post("/simulator/stop")
async def stop_simulator(server: ServerDep) -> Dict[str, Any]:
    """停止模拟器"""
    service = _get_simulator_service(server)
    if service is None:
        raise HTTPException(status_code=404, detail="模拟器服务未加载")
    if not service.is_running:
        return {"status": "already_stopped"}
    await service.stop()
    return {"status": "stopped"}


@router.post("/simulator/params")
async def update_params(params: SimulatorParams, server: ServerDep) -> Dict[str, Any]:
    """更新运行时参数"""
    sim = _get_simulator(server)
    if sim is None:
        raise HTTPException(status_code=404, detail="模拟器服务未加载")
    update_dict = params.model_dump(exclude_none=True)
    if update_dict:
        sim.update_runtime_config(**update_dict)
    return {"status": "updated", "params": update_dict}


@router.post("/simulator/trigger/gift_rain")
async def trigger_gift_rain(payload: TriggerPayload, server: ServerDep) -> Dict[str, Any]:
    """手动触发礼物雨"""
    sim = _get_simulator(server)
    if sim is None:
        raise HTTPException(status_code=404, detail="模拟器服务未加载")
    sim.trigger_gift_rain(payload.duration_s)
    return {"status": "gift_rain_triggered", "duration_s": payload.duration_s}


@router.post("/simulator/trigger/topic_injection")
async def trigger_topic_injection(payload: TriggerPayload, server: ServerDep) -> Dict[str, Any]:
    """注入话题到上下文"""
    sim = _get_simulator(server)
    if sim is None:
        raise HTTPException(status_code=404, detail="模拟器服务未加载")
    sim.trigger_topic_injection(payload.topic)
    return {"status": "topic_injected", "topic": payload.topic}


@router.post("/simulator/reset_token_budget")
async def reset_token_budget(server: ServerDep) -> Dict[str, Any]:
    """重置 Token 预算"""
    sim = _get_simulator(server)
    if sim is None:
        raise HTTPException(status_code=404, detail="模拟器服务未加载")
    sim.reset_token_budget()
    return {"status": "budget_reset"}


# --- WebSocket ---


@router.websocket("/ws/simulator/stream")
async def ws_simulator_stream(websocket: WebSocket):
    """WebSocket 实时推送模拟器生成的消息"""
    await websocket.accept()
    try:
        server = get_dashboard_server()
    except Exception:
        await websocket.close(code=1011)
        return

    sim = _get_simulator(server)
    if sim is None:
        await websocket.send_json({"error": "模拟器服务未加载"})
        await websocket.close(code=1011)
        return

    queue = sim.subscribe_for_ws()
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
            except asyncio.CancelledError:
                break
            except asyncio.TimeoutError:
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        sim.unsubscribe_ws(queue)
