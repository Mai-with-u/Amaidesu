"""Dashboard API 层共享辅助函数

供 rundown / rundowns / streamer 等 API 模块共用的最小工具集；
从各模块的本地复刻收敛而来，新增共用逻辑优先放这里。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from src.modules.logging import get_logger

logger = get_logger("DashboardAPICommon")

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer


def resolve_streamer_agent(server: "DashboardServer") -> Optional[Any]:
    """从 agent_manager 中取出 ``streamer`` 实例；无则返回 None。"""
    am = getattr(server, "agent_manager", None)
    if am is None:
        return None
    try:
        return am.get_agent_by_name("streamer")
    except Exception:
        logger.warning("解析 streamer Agent 实例失败，按未装配处理", exc=True)
        return None
