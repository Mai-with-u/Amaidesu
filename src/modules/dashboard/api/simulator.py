"""
Simulator API（世界模拟器三模式控制面）

暴露 ``SimulatorService`` 的状态查询、启停控制与运行时数据 CRUD：
- ``GET    /api/v1/simulator/status``       —— enabled / running / mode + 回放进度 + 配置摘要
- ``POST   /api/v1/simulator/start``        —— 按当前 mode 启动世界循环（可带回放日期）
- ``POST   /api/v1/simulator/stop``         —— 停止世界循环（幂等）
- ``GET    /api/v1/simulator/replay/dates`` —— 可回放的录制日期列表
- ``GET    /api/v1/simulator/personas``     —— 常驻人设列表
- ``POST   /api/v1/simulator/personas``     —— 新增常驻人设
- ``PATCH  /api/v1/simulator/personas/{id}``—— 更新常驻人设
- ``DELETE /api/v1/simulator/personas/{id}``—— 删除常驻人设
- ``GET    /api/v1/simulator/gifts``        —— 礼物目录列表
- ``POST   /api/v1/simulator/gifts``        —— 新增礼物
- ``PATCH  /api/v1/simulator/gifts/{id}``   —— 更新礼物
- ``DELETE /api/v1/simulator/gifts/{id}``   —— 删除礼物

设计取舍（与本仓库其它 API 端点对齐）：
- 仅在 ``[simulator].enabled=true`` 时组合根会装配 ``SimulatorService``；此处允许
  注入为 None（如默认生产模式），端点返回 ``{enabled: false, is_available: false}``
  而不是 404 —— 控制面要与"未启用"区分清楚。
- 人设/礼物 CRUD 走 ``SimulatorService`` 持有的 ``PersonaPool`` / ``GiftGenerator``
  （DB 写穿 + 内存缓存刷新），Dashboard 不直接持有 SQLiteStore。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.events.names import CoreEvents
from src.modules.logging import get_logger
from src.modules.storage.sqlite_store import sqlite_store as get_default_store

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer
    from src.modules.simulator.service import SimulatorService


router = APIRouter()
logger = get_logger("SimulatorAPI")

ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


def _get_service(server: "DashboardServer") -> Optional["SimulatorService"]:
    """从 DashboardServer 取 SimulatorService（无注入则 None）。"""
    return getattr(server, "simulator_service", None)


def _config_enabled(server: "DashboardServer") -> bool:
    """读 [simulator].enabled 配置开关（不依赖实例是否存在）。"""
    if not server.config_service:
        return False
    try:
        section = server.config_service.get_section("simulator", default={}) or {}
    except Exception:
        return False
    if not isinstance(section, dict):
        return False
    return bool(section.get("enabled", False))


def _config_summary(server: "DashboardServer") -> Dict[str, Any]:
    """从 [simulator] 段派生只读配置摘要（前端只展示，不修改）。

    服务尚未 ``setup()`` 时只能从配置 dict 读 key-value；setup 完成后
    优先以 ``service._config_obj.model_dump()`` 返回 Pydantic 校验过的字段集。
    """
    service = _get_service(server)
    if service is not None and getattr(service, "_config_obj", None) is not None:
        try:
            dump = service._config_obj.model_dump()
            return {k: dump.get(k) for k in _CONFIG_SUMMARY_KEYS if k in dump}
        except Exception as exc:  # noqa: BLE001 - 边界
            logger.debug(f"simulator._config_obj.model_dump 失败，回退 raw dict: {exc}")

    if not server.config_service:
        return {}
    try:
        section = server.config_service.get_section("simulator", default={}) or {}
    except Exception:
        return {}
    if not isinstance(section, dict):
        return {}
    return {k: section.get(k) for k in _CONFIG_SUMMARY_KEYS if k in section}


# 控制面只展示的关键配置项（避免泄露完整原始 dict；其余字段在面板不消费）
_CONFIG_SUMMARY_KEYS = (
    "enabled",
    "mode",
    "replay_date",
    "replay_speed",
    "replay_simulated_only",
    "base_rate_per_minute",
    "burst_multiplier",
    "gift_probability",
    "sc_probability",
    "llm_client_type",
    "llm_temperature",
    "token_budget_per_hour",
    "max_concurrent_llm",
    "cadence_mode",
    "fixed_interval_s",
    "enable_hater",
    "language",
    "fallback_session_id",
    "session_strategy",
)


class SimulatorStartRequest(BaseModel):
    """启动请求体（replay 模式的录制日期覆盖，其余模式忽略）。"""

    replay_date: Optional[str] = Field(default=None, description="录制日期 YYYY-MM-DD（replay 模式用）")


class PersonaCreateRequest(BaseModel):
    """新增常驻人设请求体（user_id 由服务端生成）。"""

    user_nickname: str = Field(min_length=1, max_length=50)
    role: str = Field(pattern="^(fan|teaser|newcomer|hater|veteran|passerby)$")
    personality: str = Field(min_length=1)
    speaking_style: str = Field(min_length=1)
    fans_medal_level: int = Field(default=0, ge=0, le=40)
    guard_level: int = Field(default=0, ge=0, le=3)
    context_window_size: Optional[int] = Field(default=None, ge=1, le=50)


class PersonaUpdateRequest(BaseModel):
    """按字段更新常驻人设请求体（None 字段不更新）。"""

    user_nickname: Optional[str] = Field(default=None, min_length=1, max_length=50)
    role: Optional[str] = Field(default=None, pattern="^(fan|teaser|newcomer|hater|veteran|passerby)$")
    personality: Optional[str] = None
    speaking_style: Optional[str] = None
    fans_medal_level: Optional[int] = Field(default=None, ge=0, le=40)
    guard_level: Optional[int] = Field(default=None, ge=0, le=3)
    context_window_size: Optional[int] = Field(default=None, ge=1, le=50)
    is_active: Optional[bool] = None


class GiftCreateRequest(BaseModel):
    """新增礼物请求体。"""

    gift_id: str = Field(min_length=1, max_length=64, pattern="^[a-zA-Z0-9_]+$")
    gift_name: str = Field(min_length=1, max_length=50)
    category: str = Field(pattern="^(normal|medium|premium|sc)$")
    weight: int = Field(default=1, ge=1)
    data_type: str = Field(default="gift", pattern="^(gift|super_chat)$")
    sc_amount_rmb: Optional[int] = Field(default=None, ge=1)


class GiftUpdateRequest(BaseModel):
    """按字段更新礼物请求体（None 字段不更新）。"""

    gift_name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    category: Optional[str] = Field(default=None, pattern="^(normal|medium|premium|sc)$")
    weight: Optional[int] = Field(default=None, ge=1)
    data_type: Optional[str] = Field(default=None, pattern="^(gift|super_chat)$")
    sc_amount_rmb: Optional[int] = Field(default=None, ge=1)


@router.get("/status", summary="模拟器状态（enabled / is_available / is_running / mode + 回放进度 + 配置摘要）")
async def get_simulator_status(server: ServerDep) -> Dict[str, Any]:
    """读取模拟器当前状态。

    返回：
        {
            "enabled":          bool,      # [simulator].enabled 开关
            "is_available":     bool,      # SimulatorService 实例是否存在（setup 后即 True）
            "is_running":       bool,      # 当前是否在世界循环里
            "mode":             str,       # 当前运行模式（off/generate/replay）
            "replay_progress":  dict|null, # replay 模式进度（date/total/remaining）
            "message":          str,       # 给前端的状态说明
            "config":           { ... },   # 只读配置摘要
        }

    不会抛 404——enabled=false 时返回 ``is_available=false``，前端可据此渲染空态引导。
    """
    enabled = _config_enabled(server)
    service = _get_service(server)
    is_available = service is not None
    is_running = bool(getattr(service, "is_running", False))
    mode = str(getattr(service, "mode", "off"))
    replay_progress = getattr(service, "replay_progress", None)

    if not enabled:
        message = "[simulator].enabled=false；模拟器未启用。请在 config/core.toml 的 [simulator] 段将 enabled 设为 true 并重启。"
    elif not is_available:
        message = "配置启用但 SimulatorService 未注入（SQLiteStore/LLMManager 缺失或 --dry 模式）。"
    elif is_running:
        message = f"模拟器正在运行（mode={mode}）。"
    else:
        message = "模拟器已装配但未运行。"

    return {
        "enabled": enabled,
        "is_available": is_available,
        "is_running": is_running,
        "mode": mode,
        "replay_progress": replay_progress,
        "message": message,
        "config": _config_summary(server),
    }


@router.post("/start", summary="按当前 mode 启动世界循环（generate/replay）")
async def start_simulator(server: ServerDep, request: Optional[SimulatorStartRequest] = None) -> Dict[str, Any]:
    """启动模拟器（需 enabled；幂等：已在运行时返回 success=True）。

    replay 模式可传 ``replay_date`` 覆盖配置中的默认录制日期。
    """
    enabled = _config_enabled(server)
    if not enabled:
        return {
            "success": False,
            "message": "[simulator].enabled=false；无法启动。请修改配置后重启应用。",
        }
    service = _get_service(server)
    if service is None:
        return {
            "success": False,
            "message": "SimulatorService 未注入（通常因 SQLiteStore/LLMManager 缺失或 --dry 模式）。",
        }
    if getattr(service, "is_running", False):
        return {"success": True, "message": "模拟器已在运行", "is_running": True}

    replay_date = request.replay_date if request is not None else None
    try:
        await service.start(replay_date=replay_date)
    except Exception as exc:  # noqa: BLE001 - 边界
        logger.error(f"模拟器启动失败: {exc}", exc_info=True)
        return {"success": False, "message": f"启动失败: {exc}"}

    if not getattr(service, "is_running", False):
        return {
            "success": False,
            "message": "启动未生效（mode=off 或 replay 缺少可用录制日期），详见应用日志。",
            "is_running": False,
        }

    return {
        "success": True,
        "message": "模拟器已启动",
        "is_running": True,
        "mode": getattr(service, "mode", "off"),
        "replay_progress": getattr(service, "replay_progress", None),
    }


@router.post("/stop", summary="停止模拟器生成循环（幂等）")
async def stop_simulator(server: ServerDep) -> Dict[str, Any]:
    """停止模拟器（幂等：未运行时返回 success=True）。"""
    service = _get_service(server)
    if service is None:
        # 未注入等价于未运行 —— 幂等返回成功，避免前端反复点停出现误导
        return {
            "success": True,
            "message": "SimulatorService 未注入，视为未运行",
            "is_running": False,
        }
    if not getattr(service, "is_running", False):
        return {"success": True, "message": "模拟器未运行", "is_running": False}

    try:
        await service.stop()
    except Exception as exc:  # noqa: BLE001 - 边界
        logger.error(f"模拟器停止失败: {exc}", exc_info=True)
        return {"success": False, "message": f"停止失败: {exc}"}

    return {
        "success": True,
        "message": "模拟器已停止",
        "is_running": bool(getattr(service, "is_running", False)),
    }


# ------------------------------------------------------------------ #
# 录制回放：日期清单                                                  #
# ------------------------------------------------------------------ #


@router.get("/replay/dates", summary="可回放的录制日期列表（event_history 表）")
async def list_replay_dates(server: ServerDep) -> Dict[str, Any]:
    """列出有弹幕录制记录的日期（按时间正序），供回放选择器使用。

    数据源为 ``event_history`` 表（与 SimulatorService 实例无关），经
    默认 store 工厂取连接；enabled=false 也可用。
    """
    dates = await get_default_store().list_event_dates(CoreEvents.ROOM_MESSAGE_DANMAKU)
    return {"dates": dates}


# ------------------------------------------------------------------ #
# 常驻人设 CRUD（经 PersonaPool 写穿 DB）                             #
# ------------------------------------------------------------------ #


def _require_pool(server: "DashboardServer") -> Any:
    service = _get_service(server)
    if service is None:
        return None
    return getattr(service, "persona_pool", None)


def _require_gift_generator(server: "DashboardServer") -> Any:
    service = _get_service(server)
    if service is None:
        return None
    return getattr(service, "gift_generator", None)


def _persona_dump(persona: Any) -> Dict[str, Any]:
    return persona.model_dump()


@router.get("/personas", summary="常驻人设列表")
async def list_personas(server: ServerDep) -> Dict[str, Any]:
    pool = _require_pool(server)
    if pool is None:
        return {"personas": [], "is_available": False}
    return {
        "personas": [_persona_dump(p) for p in pool.list_residents()],
        "is_available": True,
    }


@router.post("/personas", summary="新增常驻人设")
async def create_persona(server: ServerDep, request: PersonaCreateRequest) -> Dict[str, Any]:
    from src.modules.simulator.types import Persona, PersonaRole

    pool = _require_pool(server)
    if pool is None:
        return {"success": False, "message": "模拟器未装配（enabled=false 或未 setup）"}
    persona = Persona(
        user_id=f"sim_{uuid.uuid4().hex[:8]}",
        user_nickname=request.user_nickname,
        role=PersonaRole(request.role),
        personality=request.personality,
        speaking_style=request.speaking_style,
        fans_medal_level=request.fans_medal_level,
        guard_level=request.guard_level,
        context_window_size=request.context_window_size,
    )
    added = await pool.add_personas([persona])
    if added == 0:
        return {"success": False, "message": f"昵称已存在: {request.user_nickname}"}
    logger.info(f"已新增常驻人设: {request.user_nickname}")
    return {"success": True, "message": "已新增", "persona": _persona_dump(persona)}


@router.patch("/personas/{user_id}", summary="更新常驻人设（仅传入字段被更新）")
async def update_persona(server: ServerDep, user_id: str, request: PersonaUpdateRequest) -> Dict[str, Any]:
    pool = _require_pool(server)
    if pool is None:
        return {"success": False, "message": "模拟器未装配（enabled=false 或未 setup）"}
    fields = {k: v for k, v in request.model_dump().items() if v is not None}
    if not fields:
        return {"success": False, "message": "无可更新字段"}
    updated = await pool.update_persona(user_id, fields)
    if not updated:
        return {"success": False, "message": f"人设不存在: {user_id}"}
    return {"success": True, "message": "已更新"}


@router.delete("/personas/{user_id}", summary="删除常驻人设")
async def delete_persona(server: ServerDep, user_id: str) -> Dict[str, Any]:
    pool = _require_pool(server)
    if pool is None:
        return {"success": False, "message": "模拟器未装配（enabled=false 或未 setup）"}
    deleted = await pool.delete_persona(user_id)
    if not deleted:
        return {"success": False, "message": f"人设不存在: {user_id}"}
    return {"success": True, "message": "已删除"}


# ------------------------------------------------------------------ #
# 礼物目录 CRUD（经 GiftGenerator 写穿 DB）                           #
# ------------------------------------------------------------------ #


@router.get("/gifts", summary="礼物目录列表")
async def list_gifts(server: ServerDep) -> Dict[str, Any]:
    gen = _require_gift_generator(server)
    if gen is None:
        return {"gifts": [], "is_available": False}
    return {
        "gifts": [g.model_dump() for g in gen.list_gifts()],
        "is_available": True,
    }


@router.post("/gifts", summary="新增礼物")
async def create_gift(server: ServerDep, request: GiftCreateRequest) -> Dict[str, Any]:
    from src.modules.simulator.types import GiftItem

    gen = _require_gift_generator(server)
    if gen is None:
        return {"success": False, "message": "模拟器未装配（enabled=false 或未 setup）"}
    gift = GiftItem(
        gift_id=request.gift_id,
        gift_name=request.gift_name,
        category=request.category,
        weight=request.weight,
        data_type=request.data_type,
        sc_amount_rmb=request.sc_amount_rmb,
    )
    added = await gen.add_gift(gift)
    if not added:
        return {"success": False, "message": f"gift_id 已存在: {request.gift_id}"}
    logger.info(f"已新增礼物: {request.gift_name}")
    return {"success": True, "message": "已新增", "gift": gift.model_dump()}


@router.patch("/gifts/{gift_id}", summary="更新礼物（仅传入字段被更新）")
async def update_gift(server: ServerDep, gift_id: str, request: GiftUpdateRequest) -> Dict[str, Any]:
    gen = _require_gift_generator(server)
    if gen is None:
        return {"success": False, "message": "模拟器未装配（enabled=false 或未 setup）"}
    fields = {k: v for k, v in request.model_dump().items() if v is not None}
    if not fields:
        return {"success": False, "message": "无可更新字段"}
    try:
        updated = await gen.update_gift(gift_id, fields)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    if not updated:
        return {"success": False, "message": f"礼物不存在: {gift_id}"}
    return {"success": True, "message": "已更新"}


@router.delete("/gifts/{gift_id}", summary="删除礼物")
async def delete_gift(server: ServerDep, gift_id: str) -> Dict[str, Any]:
    gen = _require_gift_generator(server)
    if gen is None:
        return {"success": False, "message": "模拟器未装配（enabled=false 或未 setup）"}
    deleted = await gen.delete_gift(gift_id)
    if not deleted:
        return {"success": False, "message": f"礼物不存在: {gift_id}"}
    return {"success": True, "message": "已删除"}
