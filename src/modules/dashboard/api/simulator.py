"""
Simulator API（ADR-006：LLM 模拟器控制面）

暴露 ``SimulatorService`` 的状态查询与启停控制：
- ``GET  /api/v1/simulator/status``   —— 当前 enabled / running / 已初始化标记 + 配置摘要
- ``POST /api/v1/simulator/start``    —— 启动生成循环（需 ``enabled=true``，当前未运行）
- ``POST /api/v1/simulator/stop``     —— 停止生成循环（幂等）

设计取舍（与本仓库其它 API 端点对齐）：
- 仅在 ``[simulator].enabled=true`` 时组合根会装配 ``SimulatorService``；此处允许
  注入为 None（如默认生产模式），端点返回 ``{enabled: false, is_available: false}``
  而不是 404 —— 控制面要与"未启用"区分清楚。
- ``is_available`` 表示当前进程内存里是否持有 ``SimulatorService`` 实例（用于
  区分"配置启用但 LLMManager 未注入导致 setup 提前返回"与"完全没装"的两种情况）。
- ``start`` / ``stop`` 通过 ``run_shutdown`` 同源路径（``service.start`` /
  ``service.stop``），不动配置；启用开关仍由组合根读取 ``[simulator].enabled``
  （动态切换需重启，仅在 Dashboard 控制面给出引导文案）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.logging import get_logger

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


@router.get("/status", summary="模拟器状态（enabled / is_available / is_running + 配置摘要）")
async def get_simulator_status(server: ServerDep) -> Dict[str, Any]:
    """读取模拟器当前状态。

    返回：
        {
            "enabled":          bool,    # [simulator].enabled 开关
            "is_available":     bool,    # SimulatorService 实例是否存在（setup 后即 True）
            "is_running":       bool,    # 当前是否在生成循环里
            "message":          str,     # 给前端的状态说明（如"已禁用 / LLMManager 未注入"）
            "config":           { ... }, # 只读配置摘要
        }

    不会抛 404——enabled=false 时返回 ``is_available=false``，前端可据此渲染空态引导。
    """
    enabled = _config_enabled(server)
    service = _get_service(server)
    is_available = service is not None
    is_running = bool(getattr(service, "is_running", False))

    if not enabled:
        message = "[simulator].enabled=false；模拟器未启用。请在 config/core.toml 的 [simulator] 段将 enabled 设为 true 并重启。"
    elif not is_available:
        message = "配置启用但 SimulatorService 未注入（LLMManager 缺失或 --dry 模式）。"
    elif is_running:
        message = "模拟器正在运行。"
    else:
        message = "模拟器已装配但未运行。"

    return {
        "enabled": enabled,
        "is_available": is_available,
        "is_running": is_running,
        "message": message,
        "config": _config_summary(server),
    }


@router.post("/start", summary="启动模拟器生成循环")
async def start_simulator(server: ServerDep) -> Dict[str, Any]:
    """启动模拟器（需 enabled；幂等：已在运行时返回 success=True）。"""
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
            "message": "SimulatorService 未注入（通常因 LLMManager 缺失或 --dry 模式）。",
        }
    if getattr(service, "is_running", False):
        return {"success": True, "message": "模拟器已在运行", "is_running": True}

    try:
        await service.start()
    except Exception as exc:  # noqa: BLE001 - 边界
        logger.error(f"模拟器启动失败: {exc}", exc_info=True)
        return {"success": False, "message": f"启动失败: {exc}"}

    return {
        "success": True,
        "message": "模拟器已启动",
        "is_running": bool(getattr(service, "is_running", False)),
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
