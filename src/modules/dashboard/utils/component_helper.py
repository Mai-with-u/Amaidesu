"""
阶段参与者辅助函数

提供 Collector/Decider/Handler 状态查询的公共接口。
基于 ManagerStatusProvider 协议与阶段层解耦。

v2 另提供基于配置全集 + 运行时 Manager 的摘要构建函数：
- 配置全集 = agents.toml [agents] 子键 / tools.toml [tools.perception.config] /
  [tools.output.config] 子键（"可用组件"清单）
- 未在启用列表的组件以 is_enabled=False 占位（组件管理页可快速启用）
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


# =============================================================================
# v2 配置全集构建（组件管理页主数据源）
# =============================================================================


def get_v2_component_list(config_main: Optional[Dict[str, Any]], server: Any) -> Dict[str, List[ComponentSummary]]:
    """构建 v2 三组组件列表（采集器 / Agent / 工具）。

    数据源：
    - 采集器：tools.toml ``[tools.perception.config]`` 的子键（感知源全集）
    - Agent：agents.toml ``[agents]`` 的子键（enabled 之外的键）
    - 工具：tools.toml ``[tools.output.config]`` 的子键（输出工具全集）

    未在启用列表中的组件以 ``is_enabled=False`` 占位，组件管理页可快速启停。
    运行时状态（is_started）优先取 Manager 实际状态。

    Args:
        config_main: 拍平后的主配置 dict（ConfigService.main_config）。
        server: DashboardServer（借助 collector_manager / agent_manager / tool_registry）。
    """
    config_main = config_main or {}
    running_collectors = _running_names(getattr(server, "collector_manager", None), "list_running")
    running_agents = _running_names(getattr(server, "agent_manager", None), "list_running")

    collectors = _build_from_config(
        config_root=_nested(config_main, "tools", "perception", "config"),
        enabled_list=_nested(config_main, "tools", "perception", "config", "enabled") or [],
        group="collectors",
        phase="input",
        component_type="collector",
        running_names=running_collectors,
    )
    agents = _build_agent_components(
        agents_section=config_main.get("agents") or {},
        running_names=running_agents,
    )
    tools = _build_from_config(
        config_root=_nested(config_main, "tools", "output", "config"),
        enabled_list=_nested(config_main, "tools", "output", "config", "enabled") or [],
        group="tools",
        phase="output",
        component_type="tool",
        running_names=set(),
    )
    return {"collectors": collectors, "agents": agents, "tools": tools}


def _running_names(manager: Any, method: str) -> set[str]:
    getter = getattr(manager, method, None)
    if getter is None:
        return set()
    try:
        return set(getter())
    except TypeError:
        return set()


def _nested(config: Dict[str, Any], *keys: str) -> Any:
    val: Any = config
    for key in keys:
        if not isinstance(val, dict):
            return {}
        val = val.get(key) or {}
    return val


def _build_from_config(
    config_root: Dict[str, Any],
    enabled_list: list[str],
    *,
    group: str,
    phase: str,
    component_type: str,
    running_names: set[str],
) -> List[ComponentSummary]:
    components: List[ComponentSummary] = []
    for name in config_root:
        if name == "enabled":
            continue
        is_enabled = name in enabled_list
        components.append(
            ComponentSummary(
                name=name,
                phase=phase,
                group=group,
                type=component_type,
                is_started=is_enabled and name in running_names,
                is_enabled=is_enabled,
            )
        )
    return components


def _build_agent_components(
    agents_section: Dict[str, Any],
    running_names: set[str],
) -> List[ComponentSummary]:
    enabled_list = list(agents_section.get("enabled") or [])
    components: List[ComponentSummary] = []
    for name in agents_section:
        if name == "enabled":
            continue
        is_enabled = name in enabled_list
        components.append(
            ComponentSummary(
                name=name,
                phase="decision",
                group="agents",
                type="agent",
                is_started=is_enabled and name in running_names,
                is_enabled=is_enabled,
            )
        )
    return components
