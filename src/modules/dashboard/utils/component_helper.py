"""
阶段参与者辅助函数

提供 Collector/Decider/Handler 状态查询的公共接口。
基于 ManagerStatusProvider 协议与阶段层解耦。
"""

from typing import Any, Dict, List, Optional

from src.modules.dashboard.schemas.component import ComponentSummary
from src.modules.dashboard.schemas.manager_protocol import ManagerStatusProvider


def get_collector_summaries(manager: Optional[ManagerStatusProvider]) -> List[ComponentSummary]:
    """从任意实现了 ManagerStatusProvider 的 Input 阶段 Manager 提取 Collector 信息"""
    if not manager:
        return []
    summaries = manager.get_component_summaries()
    return [ComponentSummary(**s) for s in summaries if s.get("phase") == "input"]


def get_decider_summaries(manager: Optional[ManagerStatusProvider]) -> List[ComponentSummary]:
    """从任意实现了 ManagerStatusProvider 的 Decision 阶段 Manager 提取 Decider 信息"""
    if not manager:
        return []
    summaries = manager.get_component_summaries()
    return [ComponentSummary(**s) for s in summaries if s.get("phase") == "decision"]


def get_handler_summaries(manager: Optional[ManagerStatusProvider]) -> List[ComponentSummary]:
    """从任意实现了 ManagerStatusProvider 的 Output 阶段 Manager 提取 Handler 信息"""
    if not manager:
        return []
    summaries = manager.get_component_summaries()
    return [ComponentSummary(**s) for s in summaries if s.get("phase") == "output"]


def get_component_detail(
    phase: str,
    name: str,
    input_manager: Optional[ManagerStatusProvider],
    decision_manager: Optional[ManagerStatusProvider],
    output_manager: Optional[ManagerStatusProvider],
) -> Optional[Dict[str, Any]]:
    """获取单个阶段参与者详情"""
    if phase == "input":
        return _find_detail(input_manager, name, "input")
    elif phase == "decision":
        return _find_detail(decision_manager, name, "decision")
    elif phase == "output":
        return _find_detail(output_manager, name, "output")
    return None


def _find_detail(manager: Optional[ManagerStatusProvider], name: str, phase: str) -> Optional[Dict[str, Any]]:
    if not manager:
        return None
    for s in manager.get_component_summaries():
        if s.get("phase") == phase and s.get("name") == name:
            return dict(s)
    return None
