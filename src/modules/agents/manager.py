"""
AgentManager —— 框架 Agent 统一管理（Wave 3 / §1.49）

- 注册 Agent（按名；重复 → 跳过后注册）
- 统一启动 / 停止 / cleanup
- 心跳 + 监控 + 可重建性（崩溃重启前提）

工具聚合责任边界：
- Agent 子类在 ``_on_start`` / ``_register_tools`` 内部构造 ToolProvider 并调用
  ``registry.register_provider(...)``（所有权内聚到 Agent 包内）。
- AgentManager 仅提供 ``audit_tools(registry)`` 纯只读审计：列出 Agent 已声明
  但 registry 未注册的工具名，便于组合根在启动后日志告警。
- **不再**提供 ``register_all_tools`` / ``collect_tool_specs`` —— 框架侧合成占位
  实现桥接会污染真实注册路径，违反 Agent 主体性。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.modules.agents.base import AgentState, BaseAgent
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
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

    # -------------------- 工具审计 --------------------

    def audit_tools(self, registry: ToolRegistry) -> List[str]:
        """审计：列出 Agent 已声明但 registry 未注册的工具名（纯只读）。

        遍历所有已注册 Agent 的 ``list_tools()`` 声明；对每个 spec name：
        - 若 ``registry.has(name)`` 为 False，加入缺失列表。
        - 若同名 spec 被 2+ 个 Agent 声明，记 warning，**不**在缺失列表中重复
          （重复声明对"是否已注册"无影响）。

        Args:
            registry: 工具注册中心（实际项目里的 ``ToolRegistry`` 实例）。

        Returns:
            排序后的缺失 spec name 列表（每个 name 至多出现一次）。

        防御：
        - 若 ``agent.list_tools()`` 抛异常 → log warning + skip 该 Agent（不污染审计）。
        - 若 ``list_tools()`` 返回 ``None`` → 跳过该 Agent（不计入）。
        """
        missing: List[str] = []
        # 跨 Agent 的同名声明追踪：name -> 首次声明的 Agent 注册名
        declared_by: Dict[str, str] = {}

        for agent_name, reg in self._agents.items():
            try:
                spec_iter = reg.agent.list_tools()
            except Exception as exc:  # noqa: BLE001 - 审计边界，失败不中断
                logger.warning(
                    f"Agent '{agent_name}' 的 list_tools() 抛异常，已跳过审计: {exc}",
                    exc_info=True,
                )
                continue

            if spec_iter is None:
                continue

            for spec in spec_iter:
                name = spec.name
                first_agent = declared_by.get(name)
                if first_agent is not None:
                    logger.warning(
                        f"工具 '{name}' 被多个 Agent 声明：'{first_agent}' 与 "
                        f"'{agent_name}'；审计按首次声明记，重复声明不再计入缺失列表"
                    )
                    continue
                declared_by[name] = agent_name
                if not registry.has(name):
                    missing.append(name)

        return sorted(missing)


__all__ = ["AgentManager", "AgentRegistration"]
