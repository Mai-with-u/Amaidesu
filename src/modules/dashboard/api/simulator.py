"""
Simulator API（观众模拟器控制面）

暴露 ``SimulatorService`` 的状态查询、启停控制、运行期装配与运行时数据 CRUD：
- ``GET    /api/v1/simulator/status``       —— enabled / running / mode + 回放进度 + 配置摘要
- ``POST   /api/v1/simulator/enable``       —— 运行期装配服务（免重启，写盘持久化；不自动开始生成）
- ``POST   /api/v1/simulator/disable``      —— 停止循环并拆卸服务（免重启，写盘持久化）
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
  而不是 404 —— 控制面要与"未启用"区分清楚。运行期启用走 ``/enable``：经组合根
  注入的 ``simulator_factory`` 闭包现场装配（依赖与启动期装配同源），enabled 写盘
  持久化，免重启。
- 人设/礼物 CRUD 走 ``SimulatorService`` 持有的 ``PersonaPool`` / ``GiftGenerator``
  （DB 写穿 + 内存缓存刷新），Dashboard 不直接持有存储连接。
- 请求/响应契约定义在 ``schemas.simulator``，本模块只留路由与依赖注入。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.simulator import (
    GiftCreateRequest,
    GiftCreateResponse,
    GiftListResponse,
    GiftOut,
    GiftUpdateRequest,
    PersonaCreateRequest,
    PersonaCreateResponse,
    PersonaListResponse,
    PersonaOut,
    PersonaUpdateRequest,
    ReplayDateEntry,
    ReplayDatesResponse,
    SimulatorAssemblyResponse,
    SimulatorOperationResponse,
    SimulatorReplayProgress,
    SimulatorRunStateResponse,
    SimulatorStartRequest,
    SimulatorStatusResponse,
)
from src.modules.dashboard.services.config_adapter import apply_config_updates
from src.modules.dashboard.utils.component_helper import config_dir as get_config_dir
from src.modules.logging import get_logger
from src.modules.storage.database import sqlite_database as get_default_db

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer
    from src.modules.simulator.service import SimulatorService
    from src.modules.simulator.types import Persona


router = APIRouter()
logger = get_logger("SimulatorAPI")

ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


def _config_enabled(server: "DashboardServer") -> bool:
    """读 [simulator].enabled 配置开关（不依赖实例是否存在）。"""
    if not server.config_service:
        return False
    try:
        section = server.config_service.get_section("simulator", default={}) or {}
    except Exception:
        logger.warning("读取 [simulator] 配置段失败，按未启用处理", exc=True)
        return False
    if not isinstance(section, dict):
        return False
    return bool(section.get("enabled", False))


def _config_summary(server: "DashboardServer") -> Dict[str, Any]:
    """从 [simulator] 段派生只读配置摘要（前端只展示，不修改）。

    服务尚未 ``setup()`` 时只能从配置 dict 读 key-value；setup 完成后
    优先以 ``SimulatorService.config_snapshot()`` 返回 Pydantic 校验过的字段集。
    ``enabled`` 恒以配置文件现值为准——运行期启用后服务的 setup 期快照
    仍是装配那一刻的旧值。
    """
    service = server.simulator_service
    if service is not None:
        snapshot = service.config_snapshot()
        if snapshot is not None:
            summary = {k: snapshot.get(k) for k in _CONFIG_SUMMARY_KEYS if k in snapshot}
            summary["enabled"] = _config_enabled(server)
            return summary

    if not server.config_service:
        return {}
    try:
        section = server.config_service.get_section("simulator", default={}) or {}
    except Exception:
        logger.warning("读取 [simulator] 配置段失败，按空摘要处理", exc=True)
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


def _replay_progress_of(service: Optional["SimulatorService"]) -> Optional[SimulatorReplayProgress]:
    """读服务的回放进度并落为响应模型（无进度返回 None）。"""
    raw = service.replay_progress if service is not None else None
    if not isinstance(raw, dict):
        return None
    return SimulatorReplayProgress(
        date=raw.get("date"),
        total=int(raw.get("total") or 0),
        remaining=int(raw.get("remaining") or 0),
    )


def _status_response(server: "DashboardServer") -> SimulatorStatusResponse:
    """从 server 现状构建状态响应（status 端点与 enable/disable 端点共用）。"""
    enabled = _config_enabled(server)
    service = server.simulator_service
    is_available = service is not None
    is_running = bool(service.is_running) if service is not None else False
    mode = str(service.mode) if service is not None else "off"

    if not enabled:
        message = "[simulator].enabled=false；观众模拟器未启用。可在本页直接启用（免重启），或修改配置后重启。"
    elif not is_available:
        message = "配置启用但 SimulatorService 未注入（存储仓储/LLMManager 缺失或 --dry 模式）。"
    elif is_running:
        message = f"观众模拟器正在运行（mode={mode}）。"
    else:
        message = "观众模拟器已装配但未运行。"

    return SimulatorStatusResponse(
        enabled=enabled,
        is_available=is_available,
        is_running=is_running,
        mode=mode,
        replay_progress=_replay_progress_of(service),
        last_error=service.last_error if service is not None else None,
        message=message,
        config=_config_summary(server),
    )


@router.get("/status", summary="观众模拟器状态（enabled / is_available / is_running / mode + 回放进度 + 配置摘要）")
async def get_simulator_status(server: ServerDep) -> SimulatorStatusResponse:
    """读取观众模拟器当前状态。

    不会抛 404——enabled=false 时返回 ``is_available=false``，前端可据此渲染空态引导。
    """
    return _status_response(server)


async def _persist_enabled_flag(server: "DashboardServer", value: bool) -> Optional[str]:
    """把 [simulator].enabled 写入 infra.toml 持久化（返回错误消息，None=成功）。

    走统一配置写管线（校验 → 写盘 → 内存视图重载）；不注入热应用上下文
    ——运行期生效由 enable/disable 端点自己负责，与热应用机制无关。
    """
    if not server.config_service:
        return "配置服务不可用"
    try:
        outcome = await apply_config_updates(
            server.config_service,
            get_config_dir(server),
            [("infra.simulator.enabled", value)],
        )
    except Exception as exc:  # noqa: BLE001 - 写盘失败不阻断运行期动作，原因进消息
        logger.exception(f"[simulator].enabled 写盘失败（目标值 {value}）: {exc}")
        return f"配置写盘失败: {exc}"
    if not outcome.success:
        return outcome.message or "配置写盘失败"
    return None


@router.post(
    "/enable",
    response_model=SimulatorAssemblyResponse,
    response_model_exclude_none=True,
    summary="启用观众模拟器：运行期装配服务（免重启，写盘持久化；不自动开始生成）",
)
async def enable_simulator(server: ServerDep) -> SimulatorAssemblyResponse:
    """启用观众模拟器（免重启），镜像"重启后 enabled=true"的效果。

    服务未装配时经组合根注入的 ``simulator_factory`` 现场装配（构造 +
    setup），``[simulator].enabled`` 写入 infra.toml 使下次启动保持启用。
    启用与生成分离（ADR-050 决策 3 机制修订）：装配后待命，生成循环由
    ``/start`` 显式发起。
    """
    service = server.simulator_service
    if service is None:
        factory = server.simulator_factory
        if factory is None:
            return SimulatorAssemblyResponse(
                success=False,
                message="无法启用：运行期装配工厂未注入（极简启动或测试装配）。请将 [simulator].enabled 设为 true 后重启应用。",
                status=_status_response(server),
            )
        try:
            service = await factory()
        except Exception as exc:  # noqa: BLE001 - 边界
            logger.exception(f"模拟器运行期装配失败: {exc}")
            return SimulatorAssemblyResponse(success=False, message=f"启用失败: {exc}", status=_status_response(server))
        server.simulator_service = service

    persist_error = await _persist_enabled_flag(server, True)

    if service.is_running:
        message = "观众模拟器已启用，生成循环运行中"
    else:
        message = "观众模拟器已启用（装配完成），点上方启动开始生成循环"
    if persist_error is not None:
        message += f"；本次运行保持启用，但配置写盘失败，重启后回到未启用（{persist_error}）"
    return SimulatorAssemblyResponse(success=True, message=message, status=_status_response(server))


@router.post(
    "/disable",
    response_model=SimulatorAssemblyResponse,
    response_model_exclude_none=True,
    summary="停用观众模拟器：停止循环并拆卸服务（免重启，写盘持久化）",
)
async def disable_simulator(server: ServerDep) -> SimulatorAssemblyResponse:
    """停用观众模拟器（免重启），镜像"重启后 enabled=false"的效果。

    有服务则先停循环再清理实现对象（``cleanup()`` 内含未停先停），
    并从 DashboardServer 摘除引用；``[simulator].enabled`` 写为 false
    使下次启动回到零装配。未装配时幂等成功。
    """
    service = server.simulator_service
    if service is not None:
        try:
            await service.cleanup()
        except Exception as exc:  # noqa: BLE001 - 边界
            logger.exception(f"模拟器停用失败: {exc}")
            return SimulatorAssemblyResponse(success=False, message=f"停用失败: {exc}", status=_status_response(server))
        server.simulator_service = None

    persist_error = await _persist_enabled_flag(server, False)
    message = "观众模拟器已停用"
    if persist_error is not None:
        message += f"；本次运行保持停用，但配置写盘失败，重启后仍会装配（{persist_error}）"
    return SimulatorAssemblyResponse(success=True, message=message, status=_status_response(server))


@router.post(
    "/start",
    response_model=SimulatorRunStateResponse,
    response_model_exclude_none=True,
    summary="按当前 mode 启动世界循环（generate/replay）",
)
async def start_simulator(
    server: ServerDep, request: Optional[SimulatorStartRequest] = None
) -> SimulatorRunStateResponse:
    """启动观众模拟器（需 enabled；幂等：已在运行时返回 success=True）。

    replay 模式可传 ``replay_date`` 覆盖配置中的默认录制日期。
    """
    enabled = _config_enabled(server)
    if not enabled:
        return SimulatorRunStateResponse(
            success=False, message="[simulator].enabled=false；无法启动。可先在本页启用观众模拟器（免重启）。"
        )
    service = server.simulator_service
    if service is None:
        return SimulatorRunStateResponse(
            success=False, message="SimulatorService 未注入（通常因存储仓储/LLMManager 缺失或 --dry 模式）。"
        )
    if service.is_running:
        return SimulatorRunStateResponse(success=True, message="观众模拟器已在运行", is_running=True)

    replay_date = request.replay_date if request is not None else None
    try:
        rejected = await service.start(replay_date=replay_date)
    except Exception as exc:  # noqa: BLE001 - 边界
        logger.exception(f"模拟器启动失败: {exc}")
        return SimulatorRunStateResponse(success=False, message=f"启动失败: {exc}")

    if rejected is not None:
        return SimulatorRunStateResponse(success=False, message=rejected, is_running=False)

    if not service.is_running:
        # start 未拒绝但已不在运行态：小队列回放在启动返回前就自然放完
        engine = service.replay_engine
        total = engine.total if engine is not None else 0
        return SimulatorRunStateResponse(
            success=True,
            message=f"回放已完成（共 {total} 条）",
            is_running=False,
        )

    return SimulatorRunStateResponse(
        success=True,
        message="观众模拟器已启动",
        is_running=True,
        mode=str(service.mode),
        replay_progress=_replay_progress_of(service),
    )


@router.post(
    "/stop",
    response_model=SimulatorRunStateResponse,
    response_model_exclude_none=True,
    summary="停止观众模拟器生成循环（幂等）",
)
async def stop_simulator(server: ServerDep) -> SimulatorRunStateResponse:
    """停止观众模拟器（幂等：未运行时返回 success=True）。"""
    service = server.simulator_service
    if service is None:
        # 未注入等价于未运行 —— 幂等返回成功，避免前端反复点停出现误导
        return SimulatorRunStateResponse(success=True, message="SimulatorService 未注入，视为未运行", is_running=False)
    if not service.is_running:
        return SimulatorRunStateResponse(success=True, message="观众模拟器未运行", is_running=False)

    try:
        await service.stop()
    except Exception as exc:  # noqa: BLE001 - 边界
        logger.exception(f"模拟器停止失败: {exc}")
        return SimulatorRunStateResponse(success=False, message=f"停止失败: {exc}")

    return SimulatorRunStateResponse(success=True, message="观众模拟器已停止", is_running=bool(service.is_running))


# ------------------------------------------------------------------ #
# 录制回放：日期清单                                                  #
# ------------------------------------------------------------------ #


@router.get("/replay/dates", response_model=ReplayDatesResponse, summary="可回放的录制日期列表（含各日弹幕条数）")
async def list_replay_dates(server: ServerDep) -> ReplayDatesResponse:
    """列出有弹幕记录的日期及各日条数（按时间正序），供回放选择器预告回放规模。

    数据源为 ``live_chat`` 业务表（消息流单一事实源，与 SimulatorService
    实例无关），经默认数据库工厂取明细仓储；enabled=false 也可用。
    """
    entries = await get_default_db().chat.list_chat_dates_with_counts()
    return ReplayDatesResponse(
        dates=[ReplayDateEntry(date=entry.date, danmaku_count=entry.danmaku_count) for entry in entries]
    )


# ------------------------------------------------------------------ #
# 常驻人设 CRUD（经 PersonaPool 写穿 DB）                             #
# ------------------------------------------------------------------ #


def _require_pool(server: "DashboardServer") -> Any:
    """常驻人设池（服务未装配 / 未 setup 时 None，端点按未装配降级）。"""
    service = server.simulator_service
    return service.persona_pool if service is not None else None


def _require_gift_generator(server: "DashboardServer") -> Any:
    """礼物目录（服务未装配 / 未 setup 时 None，端点按未装配降级）。"""
    service = server.simulator_service
    return service.gift_generator if service is not None else None


def _persona_out(persona: "Persona") -> PersonaOut:
    """Persona 模型 → 响应条目（role 枚举落其字符串值）。"""
    return PersonaOut.model_validate(persona.model_dump())


@router.get("/personas", response_model=PersonaListResponse, summary="常驻人设列表")
async def list_personas(server: ServerDep) -> PersonaListResponse:
    pool = _require_pool(server)
    if pool is None:
        return PersonaListResponse(personas=[], is_available=False)
    return PersonaListResponse(
        personas=[_persona_out(p) for p in pool.list_residents()],
        is_available=True,
    )


@router.post(
    "/personas", response_model=PersonaCreateResponse, response_model_exclude_none=True, summary="新增常驻人设"
)
async def create_persona(server: ServerDep, request: PersonaCreateRequest) -> PersonaCreateResponse:
    # 函数内 import：触发 simulator 包 __init__ 会拉起 SimulatorService（LLM/存储依赖链），
    # 仅端点实际调用时加载
    from src.modules.simulator.types import Persona, PersonaRole

    pool = _require_pool(server)
    if pool is None:
        return PersonaCreateResponse(success=False, message="模拟器未装配（enabled=false 或未 setup）")
    persona = Persona(
        user_id=f"sim_{uuid.uuid4().hex[:8]}",
        user_nickname=request.user_nickname,
        role=PersonaRole(request.role),
        personality=request.personality,
        speaking_style=request.speaking_style,
        fans_medal_level=request.fans_medal_level,
        guard_level=request.guard_level,
    )
    added = await pool.add_personas([persona])
    if added == 0:
        return PersonaCreateResponse(success=False, message=f"昵称已存在: {request.user_nickname}")
    logger.info(f"已新增常驻人设: {request.user_nickname}")
    return PersonaCreateResponse(success=True, message="已新增", persona=_persona_out(persona))


@router.patch(
    "/personas/{user_id}",
    response_model=SimulatorOperationResponse,
    summary="更新常驻人设（仅传入字段被更新）",
)
async def update_persona(server: ServerDep, user_id: str, request: PersonaUpdateRequest) -> SimulatorOperationResponse:
    pool = _require_pool(server)
    if pool is None:
        return SimulatorOperationResponse(success=False, message="模拟器未装配（enabled=false 或未 setup）")
    # exclude_unset 区分"未传"与"显式 null"：未传不更新；显式 null 走池层清空回默认
    fields = {k: v for k, v in request.model_dump(exclude_unset=True).items()}
    if not fields:
        return SimulatorOperationResponse(success=False, message="无可更新字段")
    updated = await pool.update_persona(user_id, fields)
    if not updated:
        return SimulatorOperationResponse(success=False, message=f"人设不存在: {user_id}")
    return SimulatorOperationResponse(success=True, message="已更新")


@router.delete("/personas/{user_id}", response_model=SimulatorOperationResponse, summary="删除常驻人设")
async def delete_persona(server: ServerDep, user_id: str) -> SimulatorOperationResponse:
    pool = _require_pool(server)
    if pool is None:
        return SimulatorOperationResponse(success=False, message="模拟器未装配（enabled=false 或未 setup）")
    deleted = await pool.delete_persona(user_id)
    if not deleted:
        return SimulatorOperationResponse(success=False, message=f"人设不存在: {user_id}")
    return SimulatorOperationResponse(success=True, message="已删除")


# ------------------------------------------------------------------ #
# 礼物目录 CRUD（经 GiftGenerator 写穿 DB）                           #
# ------------------------------------------------------------------ #


@router.get("/gifts", response_model=GiftListResponse, summary="礼物目录列表")
async def list_gifts(server: ServerDep) -> GiftListResponse:
    gen = _require_gift_generator(server)
    if gen is None:
        return GiftListResponse(gifts=[], is_available=False)
    return GiftListResponse(
        gifts=[GiftOut.model_validate(g.model_dump()) for g in gen.list_gifts()],
        is_available=True,
    )


@router.post("/gifts", response_model=GiftCreateResponse, response_model_exclude_none=True, summary="新增礼物")
async def create_gift(server: ServerDep, request: GiftCreateRequest) -> GiftCreateResponse:
    # 函数内 import：同 create_persona，仅端点实际调用时拉起 simulator 包依赖链
    from src.modules.simulator.types import GiftItem

    gen = _require_gift_generator(server)
    if gen is None:
        return GiftCreateResponse(success=False, message="模拟器未装配（enabled=false 或未 setup）")
    gift = GiftItem(
        gift_id=request.gift_id,
        gift_name=request.gift_name,
        category=request.category,
        weight=request.weight,
        data_type=request.data_type,
        sc_amount_rmb=request.sc_amount_rmb,
        unit_price=request.unit_price,
    )
    added = await gen.add_gift(gift)
    if not added:
        return GiftCreateResponse(success=False, message=f"gift_id 已存在: {request.gift_id}")
    logger.info(f"已新增礼物: {request.gift_name}")
    return GiftCreateResponse(success=True, message="已新增", gift=GiftOut.model_validate(gift.model_dump()))


@router.patch("/gifts/{gift_id}", response_model=SimulatorOperationResponse, summary="更新礼物（仅传入字段被更新）")
async def update_gift(server: ServerDep, gift_id: str, request: GiftUpdateRequest) -> SimulatorOperationResponse:
    gen = _require_gift_generator(server)
    if gen is None:
        return SimulatorOperationResponse(success=False, message="模拟器未装配（enabled=false 或未 setup）")
    fields = {k: v for k, v in request.model_dump().items() if v is not None}
    if not fields:
        return SimulatorOperationResponse(success=False, message="无可更新字段")
    try:
        updated = await gen.update_gift(gift_id, fields)
    except ValueError as exc:
        return SimulatorOperationResponse(success=False, message=str(exc))
    if not updated:
        return SimulatorOperationResponse(success=False, message=f"礼物不存在: {gift_id}")
    return SimulatorOperationResponse(success=True, message="已更新")


@router.delete("/gifts/{gift_id}", response_model=SimulatorOperationResponse, summary="删除礼物")
async def delete_gift(server: ServerDep, gift_id: str) -> SimulatorOperationResponse:
    gen = _require_gift_generator(server)
    if gen is None:
        return SimulatorOperationResponse(success=False, message="模拟器未装配（enabled=false 或未 setup）")
    deleted = await gen.delete_gift(gift_id)
    if not deleted:
        return SimulatorOperationResponse(success=False, message=f"礼物不存在: {gift_id}")
    return SimulatorOperationResponse(success=True, message="已删除")
