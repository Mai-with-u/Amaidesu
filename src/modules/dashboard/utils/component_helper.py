"""
阶段参与者辅助函数

提供 Collector/Decider/Handler 状态查询的公共接口。
基于 ManagerStatusProvider 协议与阶段层解耦。

v2 另提供基于配置全集 + 运行时 Manager 的摘要构建函数：
- 配置全集 = agents.toml [agents] 子键 / tools.toml [tools.perception.config] /
  [tools.output.config] 子键（"可用组件"清单）
- 未在启用列表的组件以 is_enabled=False 占位（组件管理页可快速启用）

description 来源（Wave U1 / B7 增强）：
- 采集器：CollectorManager._collectors[name].description（注册时填写）
- Agent：AgentManager._agents[name].description
- 工具：ToolRegistry.list_tools() 中 ToolSpec.description
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
    description 字段在已注册实例存在时取管理器/ToolSpec 的描述，否则空串。

    Args:
        config_main: 拍平后的主配置 dict（ConfigService.main_config）。
        server: DashboardServer（借助 collector_manager / agent_manager / tool_registry）。
    """
    config_main = config_main or {}
    running_collectors = _running_names(getattr(server, "collector_manager", None), "list_running")
    running_agents = _running_names(getattr(server, "agent_manager", None), "list_running")
    collector_descriptions = _manager_descriptions(
        getattr(server, "collector_manager", None),
        "_collectors",
    )
    agent_descriptions = _manager_descriptions(
        getattr(server, "agent_manager", None),
        "_agents",
    )
    tool_descriptions = _tool_descriptions(getattr(server, "tool_registry", None))

    collectors = _build_from_config(
        config_root=_nested(config_main, "tools", "perception", "config"),
        enabled_list=_nested(config_main, "tools", "perception", "config", "enabled") or [],
        group="collectors",
        phase="input",
        component_type="collector",
        running_names=running_collectors,
        descriptions=collector_descriptions,
    )
    agents = _build_agent_components(
        agents_section=config_main.get("agents") or {},
        running_names=running_agents,
        descriptions=agent_descriptions,
    )
    tools = _build_from_config(
        config_root=_nested(config_main, "tools", "output", "config"),
        enabled_list=_nested(config_main, "tools", "output", "config", "enabled") or [],
        group="tools",
        phase="output",
        component_type="tool",
        running_names=set(),
        descriptions=tool_descriptions,
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


def _manager_descriptions(manager: Any, attr_name: str) -> Dict[str, str]:
    """从 CollectorManager / AgentManager 提取已注册实例的 description 字典。

    通过内部 ``_collectors`` / ``_agents`` 字典读取（与 ``list_running`` 等
    现有 helpers 同源；不引入新的 Manager 公开 API 以避免改动面扩散）。
    防御：manager/属性不存在 / 结构不匹配时返回空 dict。
    """
    if manager is None:
        return {}
    internal = getattr(manager, attr_name, None)
    if not isinstance(internal, dict):
        return {}
    result: Dict[str, str] = {}
    for key, reg in internal.items():
        desc = getattr(reg, "description", "") or ""
        if desc:
            result[key] = desc
    return result


def _tool_descriptions(registry: Any) -> Dict[str, str]:
    """从 ToolRegistry 提取已注册 ToolSpec 的 description 字典。

    工具规格的 description 是 LLM 视角的描述，Dashboard 组件管理页可复用。
    """
    if registry is None:
        return {}
    try:
        specs = registry.list_tools()
    except Exception:
        return {}
    result: Dict[str, str] = {}
    for spec in specs:
        desc = getattr(spec, "description", "") or ""
        if desc:
            result[spec.name] = desc
    return result


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
    descriptions: Dict[str, str],
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
                description=descriptions.get(name, ""),
            )
        )
    return components


def _build_agent_components(
    agents_section: Dict[str, Any],
    running_names: set[str],
    descriptions: Dict[str, str],
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
                description=descriptions.get(name, ""),
            )
        )
    return components
