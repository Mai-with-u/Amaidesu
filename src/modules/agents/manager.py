"""
AgentManager —— 框架 Agent 统一管理（Wave 3 / §1.49）

- 注册 Agent（按名；重复 → 跳过后注册）
- 统一启动 / 停止 / cleanup
- 暴露工具：把每个 Agent list_tools() 收集后委托给 ToolRegistry
- 心跳 + 监控 + 可重建性（崩溃重启前提）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from src.modules.agents.base import AgentState, BaseAgent
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.tools.models import ToolSpec
from src.modules.tools.registry import ToolRegistry

logger = get_logger("AgentManager")


@dataclass(slots=True)
class AgentRegistration:
    """Agent 注册项（name → agent 实例 + 元数据）"""

    name: str
    agent: BaseAgent
    description: str = ""
    registered_at_ms: int = 0
    spec_provider: str = "builtin"  # §1.5 provider 来源溯源


class AgentManager:
    """统一管理：注册 / 启动 / 监控 / 重启所有 Agent。"""

    def __init__(
        self,
        *,
        tool_registry: Optional[ToolRegistry] = None,
    ) -> None:
        self._agents: Dict[str, AgentRegistration] = {}
        self._tool_registry = tool_registry
        self._lock = asyncio.Lock()

    # -------------------- 注册 --------------------

    def register(
        self,
        agent: BaseAgent,
        *,
        description: Optional[str] = None,
        spec_provider: str = "builtin",
    ) -> bool:
        """注册一个 Agent。

        Args:
            agent: BaseAgent 实例
            description: 显示描述（默认用 agent.description）
            spec_provider: 工具来源溯源（"builtin"/"game"/"mcp"）
        """
        if not agent.name:
            logger.warning(
                f"Agent 缺少 name（class={type(agent).__name__}），由 AgentManager 注册时显式拒绝；"
                f"请在子类显式声明 name = 'xxx'"
            )
            return False
        if agent.name in self._agents:
            logger.debug(f"Agent '{agent.name}' 已注册，跳过")
            return False

        self._agents[agent.name] = AgentRegistration(
            name=agent.name,
            agent=agent,
            description=description or agent.description,
            registered_at_ms=now_ms(),
            spec_provider=spec_provider,
        )
        logger.info(f"Agent '{agent.name}' 已注册 (spec_provider={spec_provider})")
        return True

    def unregister(self, name: str) -> bool:
        """从管理器移除（仅当已停止时）。"""
        reg = self._agents.get(name)
        if reg is None:
            return False
        if reg.agent.state not in (AgentState.STOPPED, AgentState.CREATED, AgentState.ERRORED):
            logger.warning(f"Agent '{name}' 未停止，不能 unregister（state={reg.agent.state}）")
            return False
        self._agents.pop(name, None)
        logger.info(f"Agent '{name}' 已从管理器移除")
        return True

    # -------------------- 查询 --------------------

    def get(self, name: str) -> Optional[BaseAgent]:
        reg = self._agents.get(name)
        return reg.agent if reg is not None else None

    def list_agents(self) -> List[str]:
        return list(self._agents.keys())

    def list_running(self) -> List[str]:
        return [name for name, reg in self._agents.items() if reg.agent.state == AgentState.RUNNING]

    # -------------------- 动态启停（Dashboard 组件管理调用） --------------------

    def get_agent_by_name(self, name: str) -> Optional[BaseAgent]:
        """按注册名查找已加载的 Agent 实例（段名=注册名）。"""
        reg = self._agents.get(name)
        return reg.agent if reg is not None else None

    async def start_agent(self, name: str) -> bool:
        """启动（或重启）单个已注册 Agent。"""
        reg = self._agents.get(name)
        if reg is None:
            return False
        if reg.agent.state == AgentState.RUNNING:
            return True
        try:
            await reg.agent.start()
        except Exception as exc:  # noqa: BLE001 - 边界
            logger.error(f"Agent '{name}' 单实例启动失败: {exc}", exc_info=True)
            return False
        return reg.agent.state == AgentState.RUNNING

    async def stop_agent(self, name: str) -> bool:
        """停止单个 Agent：调用 stop()（不 unregister）。"""
        reg = self._agents.get(name)
        if reg is None:
            return False
        try:
            await reg.agent.stop()
        except Exception as exc:  # noqa: BLE001 - 边界
            logger.error(f"Agent '{name}' 单实例停止失败: {exc}", exc_info=True)
            return False
        return True

    async def enable_agent(
        self,
        name: str,
        config: Optional[dict] = None,
        *,
        llm_manager: Optional[object] = None,
        prompt_manager: Optional[object] = None,
        context_service: Optional[object] = None,
        event_bus: Optional[object] = None,
        tool_registry: Optional[ToolRegistry] = None,
    ) -> bool:
        """动态启用 Agent：实例化（配置段名）→ 注册 → 启动。

        段名 = 注册名：实例化后强制对齐实例 name 为段名，保证
        list_agents()/get_by_name() 与配置 enabled 列表一致。
        """
        from src.modules.agents.factory import instantiate_agent

        instance = instantiate_agent(
            name,
            config or {},
            llm_manager=llm_manager,
            prompt_manager=prompt_manager,
            context_service=context_service,
            event_bus=event_bus,
            tool_registry=tool_registry,
        )
        if instance is None:
            logger.warning(f"未实现的 Agent: {name}")
            return False
        if instance.name != name:
            instance.name = name
        if not self.register(instance):
            return False
        return await self.start_agent(name)

    async def disable_agent(self, name: str) -> bool:
        """动态停用 Agent：停止 → unregister。"""
        if not await self.stop_agent(name):
            return False
        self.unregister(name)
        logger.info(f"Agent '{name}' 已动态停用")
        return True

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._agents

    # -------------------- 生命周期 --------------------

    async def start_all(self) -> None:
        """按注册顺序启动所有 Agent。"""
        for name, reg in self._agents.items():
            try:
                await reg.agent.start()
            except Exception as exc:  # noqa: BLE001 - 边界
                logger.error(f"Agent '{name}' 启动失败: {exc}", exc_info=True)

    async def stop_all(self) -> None:
        """按注册**逆序**停止所有 Agent（LIFO 风格）。"""
        for name in reversed(list(self._agents.keys())):
            reg = self._agents.get(name)
            if reg is None:
                continue
            try:
                await reg.agent.stop()
            except Exception as exc:  # noqa: BLE001 - 边界
                logger.error(f"Agent '{name}' 停止失败: {exc}", exc_info=True)

    async def cleanup_all(self) -> None:
        """清理所有 Agent 资源。"""
        for name, reg in self._agents.items():
            try:
                await reg.agent.cleanup()
            except Exception as exc:  # noqa: BLE001 - 边界
                logger.error(f"Agent '{name}' cleanup 失败: {exc}", exc_info=True)

    # -------------------- 工具聚合 --------------------

    def collect_tool_specs(self) -> List[Tuple[ToolSpec, Callable]]:
        """从所有 Agent 收集 (ToolSpec, impl) 对。

        与 ToolRegistry 集成由调用方负责：
        ``for spec, impl in manager.collect_tool_specs(): registry.register(spec, impl)``
        """
        pairs: List[Tuple[ToolSpec, Callable]] = []
        for reg in self._agents.values():
            spec_iter = reg.agent.list_tools()
            if spec_iter is None:
                continue
            for spec in spec_iter:
                # 这里只暴露 spec；impl 通常通过一个桥接函数（基于 Agent 名）
                # 因为 ToolInvocation 直接拿到 ToolSpec 字段做参数解析
                pairs.append((spec, _make_agent_tool_bridge(reg.agent, spec.name)))
        return pairs

    def register_all_tools(self, registry: Optional[ToolRegistry] = None) -> int:
        """把 Agent 工具全部注册到 ToolRegistry（默认用构造时注入的）。"""
        target = registry if registry is not None else self._tool_registry
        if target is None:
            return 0
        count = 0
        pairs = self.collect_tool_specs()
        for spec, impl in pairs:
            ok = target.register(spec, impl)
            if ok:
                count += 1
        return count


# -------------------- 桥接：Agent → Tool --------------------


def _make_agent_tool_bridge(agent: BaseAgent, tool_name: str):
    """为 Agent 上的某个工具构造 impl 桥接（base agent 自己处理参数）。"""

    # 简单实现：实际 W4 业务层会用更细的 dispatch（每个 Agent 的 list_tools
    # 返回一组 ToolSpec，spec.parameters_schema + Agent 上 dispatch）
    # 此处基类行为：直接返回失败 ToolExecutionResult 作为占位
    async def _bridge(invocation):  # type: ignore[no-untyped-def]
        from src.modules.tools.models import ToolExecutionResult

        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            error_message=(
                f"Agent '{agent.name}' 上的工具 '{tool_name}' 未在具体子类中实现 dispatch"
                f"；请在 Agent 子类覆写 list_tools 后提供实现"
            ),
        )

    return _bridge


__all__ = ["AgentManager", "AgentRegistration"]
