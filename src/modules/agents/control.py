"""
AgentControl —— 框架级 Agent 控制

- pause / resume / shutdown / restart 框架级工具
- provider="framework"（框架内置提供，非独立源）
- 这些工具对所有 Agent 生效（BaseAgent 默认实现状态机 + 钩子）

注册方式：
```python
provider = build_agent_control_provider(manager)
tool_registry.register_provider(provider)
```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Iterable, List, Optional

from src.modules.agents.manager import AgentManager
from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import ToolProvider

logger = get_logger("AgentControl")


# -------------------- 工具规格 --------------------


_AGENT_CONTROL_SPECS: List[ToolSpec] = [
    ToolSpec(
        name="pause_agent",
        description="暂停指定 Agent（按名）。状态机切到 PAUSED，调用 _on_pause 钩子。",
        parameters_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Agent 名"},
            },
            "required": ["name"],
        },
        kind="sync",
        provider="framework",
    ),
    ToolSpec(
        name="resume_agent",
        description="恢复指定 Agent（按名）。状态机切回 RUNNING，调用 _on_resume 钩子。",
        parameters_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        kind="sync",
        provider="framework",
    ),
    ToolSpec(
        name="shutdown_agent",
        description="停机指定 Agent（按名）。比 stop 更严格：调 stop + _on_shutdown。",
        parameters_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        kind="sync",
        provider="framework",
    ),
    ToolSpec(
        name="restart_agent",
        description="重启指定 Agent（stop + 工厂重建 + start）。",
        parameters_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        kind="sync",
        provider="framework",
    ),
    ToolSpec(
        name="list_agents",
        description="列出当前已注册的 Agent 名。",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        kind="sync",
        provider="framework",
    ),
    ToolSpec(
        name="agent_state",
        description="查询指定 Agent 的状态（state + heartbeat）。",
        parameters_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        kind="sync",
        provider="framework",
    ),
]


# -------------------- AgentControl 工具（写形式，可直接 await） --------------------


class AgentControl:
    """框架级控制——直接调用接口（不进 ToolRegistry）。"""

    def __init__(self, manager: AgentManager) -> None:
        self._manager = manager

    async def pause(self, name: str) -> bool:
        agent = self._manager.get(name)
        if agent is None:
            logger.warning(f"pause_agent: 未找到 Agent '{name}'")
            return False
        await agent.pause()
        return True

    async def resume(self, name: str) -> bool:
        agent = self._manager.get(name)
        if agent is None:
            logger.warning(f"resume_agent: 未找到 Agent '{name}'")
            return False
        await agent.resume()
        return True

    async def shutdown(self, name: str) -> bool:
        agent = self._manager.get(name)
        if agent is None:
            logger.warning(f"shutdown_agent: 未找到 Agent '{name}'")
            return False
        await agent.shutdown()
        return True

    async def restart(self, name: str) -> bool:
        agent = self._manager.get(name)
        if agent is None:
            logger.warning(f"restart_agent: 未找到 Agent '{name}'")
            return False
        # stop 当前实例
        await agent.stop()
        # 工厂重建
        try:
            new_agent = agent.clone()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Agent '{name}' 工厂重建失败: {exc}", exc_info=True)
            return False
        new_agent.increment_restart_counter()
        # 替换管理器中的实例：
        #    仅替换引用，调用方需保证旧实例已停止（已 stop）
        #    并未清理 event_bus 注入；生产环境建议 Agent 自行管理重建流程
        # 通过 manager 的注册表（_agents dict）替换旧实例
        reg = self._manager._agents.get(name)  # type: ignore[attr-defined]  # noqa: SLF001
        if reg is None:
            return False
        reg.agent = new_agent  # type: ignore[attr-defined]
        # start 新实例
        await new_agent.start()
        return True

    def list_agents(self) -> List[str]:
        return self._manager.list_agents()

    def state_of(self, name: str) -> Optional[dict]:
        agent = self._manager.get(name)
        if agent is None:
            return None
        return {
            "name": agent.name,
            "state": agent.state.value,
            "heartbeat_ms": agent.heartbeat.last_heartbeat_ms,
            "restart_count": agent.restart_count,
        }


# -------------------- Provider：用于注册进 ToolRegistry --------------------


@dataclass(slots=True)
class AgentControlProvider(ToolProvider):
    """把 AgentControl 工具注册到 ToolRegistry 的 Provider。"""

    manager: AgentManager
    _control: AgentControl = field(init=False)

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category: ClassVar[str] = "framework"

    def __post_init__(self) -> None:
        self._control = AgentControl(self.manager)
        # 复制 event_bus 引用（如果 manager 上有）— 此处简化，不注入

    @property
    def name(self) -> str:
        return "AgentControlProvider"

    def list_tools(self) -> Iterable[ToolSpec]:
        return list(_AGENT_CONTROL_SPECS)

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        args = invocation.arguments or {}
        name = invocation.tool_name
        # 注册名统一带 provider 前缀（framework_）；代码内直调可能用裸名，
        # 分发前剥前缀归一（不匹配前缀时原样保留）
        local_name = name.removeprefix("framework_")
        target_name = str(args.get("name", ""))
        try:
            if local_name == "pause_agent":
                ok = await self._control.pause(target_name)
            elif local_name == "resume_agent":
                ok = await self._control.resume(target_name)
            elif local_name == "shutdown_agent":
                ok = await self._control.shutdown(target_name)
            elif local_name == "restart_agent":
                ok = await self._control.restart(target_name)
            elif local_name == "list_agents":
                return ToolExecutionResult(
                    tool_name=name,
                    success=True,
                    content=", ".join(self._control.list_agents()) or "（无 Agent）",
                )
            elif local_name == "agent_state":
                info = self._control.state_of(target_name)
                if info is None:
                    return ToolExecutionResult(
                        tool_name=name,
                        success=False,
                        error_message=f"Agent '{target_name}' 不存在",
                    )
                return ToolExecutionResult(
                    tool_name=name,
                    success=True,
                    content=str(info),
                )
            else:
                return ToolExecutionResult(
                    tool_name=name,
                    success=False,
                    error_message=f"未知 AgentControl 工具 '{name}'",
                )

            return ToolExecutionResult(
                tool_name=name,
                success=ok,
                content="OK" if ok else "FAILED",
                error_message="" if ok else f"Agent '{target_name}' 操作失败",
            )
        except Exception as exc:  # noqa: BLE001 - 边界兜底
            logger.error(f"AgentControl 工具 '{name}' 执行失败: {exc}", exc_info=True)
            return ToolExecutionResult(
                tool_name=name,
                success=False,
                error_message=f"{type(exc).__name__}: {exc}",
            )


def build_agent_control_provider(manager: AgentManager) -> AgentControlProvider:
    """工厂：构造一个 AgentControlProvider 绑定 manager。"""
    return AgentControlProvider(manager=manager)


__all__ = [
    "AgentControl",
    "AgentControlProvider",
    "build_agent_control_provider",
]
