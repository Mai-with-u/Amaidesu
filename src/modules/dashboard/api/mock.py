"""
Mock 采集器 API（ADR-006：确定性 JSONL 回放器控制面）

暴露 ``MockCollector``（``src/modules/collectors/mock/`` 注册名 ``"mock"``）的状态
查询与启停控制。代理 ``CollectorManager``，与通用 ``/api/v1/components/collectors/
<name>/control`` 端点行为一致；保留 ``/api/v1/mock/*`` 命名是因为：

1. ADR-006 把"模拟"语义收敛为唯一承载者，但 ``MockCollector``（确定性回放）作为
   一种**采集器**依然存在，并归 ``CollectorManager`` 治理；
2. 控制面 URL 路径与"模拟器"（``SimulatorService``）保持区隔（后者挂在
   ``/api/v1/simulator/*``），避免路径重名误导；
3. 历史 ``mockCollectorApi`` 客户端可平滑迁移，仅切端点前缀。

设计边界：
- ADR-006 显式移除"模拟器半吊子模式"（人设 / 参数 / 礼物雨 / 话题注入 / token
  预算）——这些端点**不补齐**，强行补齐会重新复活被推翻的设计。前端老面板若误
  调会得到 404，调用侧 try/catch 即可。
- ``MockCollector`` 当前只暴露状态 + 启停（与通用组件控制面对齐）。配置摘要
  通过 ``Collectors`` 页面查看（沿用 ``componentsApi.control``）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.collectors.base import BaseCollector
    from src.modules.collectors.manager import CollectorManager
    from src.modules.dashboard.server import DashboardServer


router = APIRouter()
logger = get_logger("MockCollectorAPI")

ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]

# ADR-006 收敛后 MockCollector 的唯一注册名（见 mock_collector.py:49）
MOCK_COLLECTOR_NAME = "mock"


class MockControlResponse(BaseModel):
    """mock 采集器启停响应（与通用 ComponentControlResponse 同语义）。"""

    success: bool
    message: str
    is_available: bool = Field(default=False, description="CollectorManager 是否持有该实例")
    is_running: bool = Field(default=False, description="当前是否运行中")


def _get_manager(server: "DashboardServer") -> Optional["CollectorManager"]:
    """取 CollectorManager（可能为 None，如采集器配置段缺失）。"""
    return getattr(server, "collector_manager", None)


def _get_mock(server: "DashboardServer") -> Optional["BaseCollector"]:
    manager = _get_manager(server)
    if manager is None:
        return None
    return manager.get_collector_by_name(MOCK_COLLECTOR_NAME)


def _is_mock_running(collector: "BaseCollector") -> bool:
    """判断 mock 采集器是否在运行（兼容层 + 基类状态机双路）。"""
    # mock 采集器覆写了 is_started 标志，基类 state 停在 CREATED —— 优先看标志
    if getattr(collector, "is_started", False):
        return True
    from src.modules.collectors.base import CollectorState

    return collector.state == CollectorState.RUNNING


def _config_snapshot(collector: "BaseCollector") -> Dict[str, Any]:
    """从已实例化的 MockCollector 摘录只读配置（log_file_path / send_interval 等）。

    仅展示 ``typed_config`` 字典化结果中控面关心的字段；落空时返回空 dict
    （例如配置未注入场景），前端相应位置显示 "—"。
    """
    typed = getattr(collector, "typed_config", None)
    if typed is None:
        return {}
    try:
        dump = typed.model_dump() if hasattr(typed, "model_dump") else dict(vars(typed))
    except Exception:
        return {}
    if not isinstance(dump, dict):
        return {}
    # 控面只读的字段全集
    keys = (
        "log_file_path",
        "send_interval",
        "loop_playback",
        "emit_semantic_events",
    )
    return {k: dump.get(k) for k in keys if k in dump}


@router.get("/status", summary="Mock 采集器状态")
async def get_mock_status(server: ServerDep) -> Dict[str, Any]:
    """读取 mock 采集器当前状态。

    返回：
        {
            "is_available":  bool,    # CollectorManager 是否持有 ``mock`` 实例
            "is_running":    bool,    # 是否正在回放
            "name":          str,     # 固定 "mock"
            "description":   str,     # 来自 Collector.description
            "config":        { ... }, # 只读配置摘要（log_file_path / send_interval 等）
            "message":       str,     # 给前端的状态说明
        }

    CollectorManager 不可用或未注册 mock 实例时返回 ``is_available=false``，
    由前端展示"未注册"空态，不抛 404。
    """
    manager = _get_manager(server)
    if manager is None:
        return {
            "is_available": False,
            "is_running": False,
            "name": MOCK_COLLECTOR_NAME,
            "description": "",
            "config": {},
            "message": "CollectorManager 未注入（采集器配置段缺失？）",
        }
    collector = manager.get_collector_by_name(MOCK_COLLECTOR_NAME)
    if collector is None:
        return {
            "is_available": False,
            "is_running": False,
            "name": MOCK_COLLECTOR_NAME,
            "description": "",
            "config": {},
            "message": 'mock 采集器未在当前配置中启用（请在 config/tools.toml 的 [tools.perception.config].enabled 添加 "mock" 后重启）。',
        }

    is_running = _is_mock_running(collector)
    return {
        "is_available": True,
        "is_running": is_running,
        "name": MOCK_COLLECTOR_NAME,
        "description": getattr(collector, "description", "") or "",
        "config": _config_snapshot(collector),
        "message": "运行中" if is_running else "已停止",
    }


@router.post("/start", response_model=MockControlResponse)
async def start_mock(server: ServerDep) -> MockControlResponse:
    """启动 mock 采集器（幂等：已运行时返回 success=True）。"""
    manager = _get_manager(server)
    if manager is None:
        return MockControlResponse(
            success=False,
            message="CollectorManager 未注入，无法启动 mock。",
            is_available=False,
        )

    collector = manager.get_collector_by_name(MOCK_COLLECTOR_NAME)
    if collector is None:
        return MockControlResponse(
            success=False,
            message='mock 采集器未注册。请先到「组件」页启用，或在配置 [tools.perception.config].enabled 添加 "mock" 后重启。',
            is_available=False,
        )

    if _is_mock_running(collector):
        return MockControlResponse(success=True, message="mock 采集器已在运行", is_available=True, is_running=True)

    try:
        ok = await manager.start_collector(MOCK_COLLECTOR_NAME)
    except Exception as exc:  # noqa: BLE001 - 边界
        logger.error(f"mock 采集器启动失败: {exc}", exc_info=True)
        return MockControlResponse(success=False, message=f"启动失败: {exc}", is_available=True)

    if not ok:
        return MockControlResponse(
            success=False,
            message="CollectorManager 启动返回 False（见日志）",
            is_available=True,
        )

    # 再次校验实际状态（start_collector 已承诺进入 RUNNING 但保险起见复核）
    running = _is_mock_running(collector)
    return MockControlResponse(success=True, message="mock 采集器已启动", is_available=True, is_running=running)


@router.post("/stop", response_model=MockControlResponse)
async def stop_mock(server: ServerDep) -> MockControlResponse:
    """停止 mock 采集器（幂等：未运行时返回 success=True）。"""
    manager = _get_manager(server)
    if manager is None:
        return MockControlResponse(
            success=False,
            message="CollectorManager 未注入，无法停止 mock。",
            is_available=False,
            is_running=False,
        )

    collector = manager.get_collector_by_name(MOCK_COLLECTOR_NAME)
    if collector is None:
        return MockControlResponse(
            success=False,
            message="mock 采集器未注册",
            is_available=False,
            is_running=False,
        )

    if not _is_mock_running(collector):
        return MockControlResponse(success=True, message="mock 采集器未运行", is_available=True, is_running=False)

    try:
        ok = await manager.stop_collector(MOCK_COLLECTOR_NAME)
    except Exception as exc:  # noqa: BLE001 - 边界
        logger.error(f"mock 采集器停止失败: {exc}", exc_info=True)
        return MockControlResponse(
            success=False,
            message=f"停止失败: {exc}",
            is_available=True,
            is_running=True,
        )

    if not ok:
        return MockControlResponse(
            success=False,
            message="CollectorManager 停止返回 False（见日志）",
            is_available=True,
            is_running=True,
        )

    return MockControlResponse(success=True, message="mock 采集器已停止", is_available=True, is_running=False)
