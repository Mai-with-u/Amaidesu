"""
阶段参与者辅助函数

提供 Collector/Decider/Handler 状态查询的公共接口。
不再使用 defensive getattr，直接调用 Manager 的公开方法。
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.modules.dashboard.schemas.component import ComponentSummary

if TYPE_CHECKING:
    from src.stages.decision.manager import DeciderManager
    from src.stages.input.manager import InputCollectorManager
    from src.stages.output.manager import OutputHandlerManager


def get_collector_summaries(manager: Optional["InputCollectorManager"]) -> List[ComponentSummary]:
    """从 InputCollectorManager 提取 Collector 信息"""
    if not manager:
        return []

    if not hasattr(manager, "get_collector_status"):
        return []

    summaries = []
    for status in manager.get_collector_status():
        summaries.append(
            ComponentSummary(
                name=status["name"],
                phase="input",
                type="collector",
                is_started=status["is_started"],
                is_enabled=True,
            )
        )

    return summaries


def get_decider_summaries(manager: Optional["DeciderManager"]) -> List[ComponentSummary]:
    """从 DeciderManager 提取 Decider 信息"""
    if not manager:
        return []

    if not hasattr(manager, "get_decider_status"):
        return []

    summaries = []
    for status in manager.get_decider_status():
        summaries.append(
            ComponentSummary(
                name=status["name"],
                phase="decision",
                type="decider",
                is_started=status["is_started"],
                is_enabled=True,
            )
        )

    return summaries


def get_handler_summaries(manager: Optional["OutputHandlerManager"]) -> List[ComponentSummary]:
    """从 OutputHandlerManager 提取 Handler 信息"""
    if not manager:
        return []

    if not hasattr(manager, "get_handler_status"):
        return []

    summaries = []
    for status in manager.get_handler_status():
        summaries.append(
            ComponentSummary(
                name=status["name"],
                phase="output",
                type="handler",
                is_started=status["is_started"],
                is_enabled=True,
            )
        )

    return summaries


def get_component_detail(
    phase: str,
    name: str,
    input_manager: Optional["InputCollectorManager"],
    decision_manager: Optional["DeciderManager"],
    output_manager: Optional["OutputHandlerManager"],
) -> Optional[Dict[str, Any]]:
    """获取单个阶段参与者详情"""
    if phase == "input":
        return _get_collector_detail(name, input_manager)
    elif phase == "decision":
        return _get_decider_detail(name, decision_manager)
    elif phase == "output":
        return _get_handler_detail(name, output_manager)
    return None


def _get_collector_detail(name: str, manager: Optional["InputCollectorManager"]) -> Optional[Dict[str, Any]]:
    """获取 Collector 详情"""
    if not manager:
        return None

    if not hasattr(manager, "get_collector_status"):
        return None

    for status in manager.get_collector_status():
        if status["name"] == name:
            return {
                "name": name,
                "phase": "input",
                "type": "collector",
                "is_started": status["is_started"],
                "is_enabled": True,
                "config": status.get("config"),
            }
    return None


def _get_decider_detail(name: str, manager: Optional["DeciderManager"]) -> Optional[Dict[str, Any]]:
    """获取 Decider 详情"""
    if not manager:
        return None

    if not hasattr(manager, "get_decider_status"):
        return None

    for status in manager.get_decider_status():
        if status["name"] == name:
            return {
                "name": name,
                "phase": "decision",
                "type": "decider",
                "is_started": status["is_started"],
                "is_enabled": True,
            }
    return None


def _get_handler_detail(name: str, manager: Optional["OutputHandlerManager"]) -> Optional[Dict[str, Any]]:
    """获取 Handler 详情"""
    if not manager:
        return None

    if not hasattr(manager, "get_handler_status"):
        return None

    for status in manager.get_handler_status():
        if status["name"] == name:
            return {
                "name": name,
                "phase": "output",
                "type": "handler",
                "is_started": status["is_started"],
                "is_enabled": True,
                "config": status.get("config"),
            }
    return None
