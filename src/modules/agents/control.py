"""
AgentControl —— 框架级 Agent 控制与委派

- pause / resume / shutdown / restart 框架级控制工具
- framework_delegate / framework_task_status：跨 Agent 委派原语
  ——派活拿回执（accepted + task_id），任务进度随时可查；指令只当自然
  语言（给目标，不给步骤），不加编排/条件分支
- provider="framework"（框架内置提供，非独立源；可见名单默认 ["*"]）

注册方式：
```python
provider = build_agent_control_provider(manager, task_ledger)
tool_registry.register_provider(provider)
```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, Iterable, List, Optional

from src.modules.agents.manager import AgentManager
from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.tasks import TaskLedger
from src.modules.time_utils import now_ms

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
    ToolSpec(
        name="delegate",
        description=(
            "把一项工作委派给另一个 Agent：给目标与自然语言指令（不给步骤），"
            "立刻返回受理回执（accepted + task_id）。任务状态变化会以事件通知你；"
            "随时可用 framework_task_status 按 task_id 查询进度与快照。"
            "目标忙时会排队，无须等待。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "目标 Agent 注册名"},
                "instruction": {"type": "string", "description": "工作指令（自然语言：目标与约束，不规定步骤）"},
            },
            "required": ["agent", "instruction"],
        },
        kind="sync",
        provider="framework",
    ),
    ToolSpec(
        name="task_status",
        description=(
            "按任务号查询异步任务（委派 / 回执型工具受理）的当前状态与快照。"
            "查询玩家工作文档（todo/notebook/近期上报）用各游戏 Agent 的读取工具，"
            "两者不重复。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "受理回执返回的任务号"},
            },
            "required": ["task_id"],
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
class AgentControlProvider(BaseToolProvider):
    """把 AgentControl 工具（控制 6 件 + 委派 2 件）注册到 ToolRegistry 的 Provider。

    委派原语：
    - ``framework_delegate(agent, instruction)``：名册校验 + **禁自派** +
      目标接收入口（BaseAgent 默认拒收）→ 受理成功登记同一张任务记录表
      （agent 型：发起方=调用方、执行者=目标、事实源=执行 Agent）
    - 受理失败（目标不存在/未启用/拒收/自派）与任务失败（执行中失败）分开
    - ``framework_task_status(task_id)``：查记录表；未知任务号 → 失败结果
    """

    manager: AgentManager
    task_ledger: Optional[TaskLedger] = None
    _control: AgentControl = field(init=False)
    _task_seq: int = field(default=0, init=False)

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category: ClassVar[str] = "framework"

    def __post_init__(self) -> None:
        self._control = AgentControl(self.manager)
        # 复制 event_bus 引用（如果 manager 上有）— 此处简化，不注入

    @property
    def name(self) -> str:
        return "framework"

    def list_tools(self) -> Iterable[ToolSpec]:
        return list(_AGENT_CONTROL_SPECS)

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        args = invocation.arguments or {}
        name = invocation.tool_name
        # 调用方使用的就是派生全名（framework_pause_agent 等），等值对照分发
        target_name = str(args.get("name", ""))
        try:
            if name == "framework_delegate":
                return await self._invoke_delegate(args, caller=invocation.source)
            if name == "framework_task_status":
                return self._invoke_task_status(args)
            if name == "framework_pause_agent":
                ok = await self._control.pause(target_name)
            elif name == "framework_resume_agent":
                ok = await self._control.resume(target_name)
            elif name == "framework_shutdown_agent":
                ok = await self._control.shutdown(target_name)
            elif name == "framework_restart_agent":
                ok = await self._control.restart(target_name)
            elif name == "framework_list_agents":
                return ToolExecutionResult(
                    tool_name=name,
                    success=True,
                    content=", ".join(self._control.list_agents()) or "（无 Agent）",
                )
            elif name == "framework_agent_state":
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

    # ----- 委派原语（framework_delegate / framework_task_status） -----

    def _next_task_id(self) -> str:
        """生成委派任务号（deleg_{epoch_ms}_{seq}，全链路关联键）。"""
        self._task_seq += 1
        return f"deleg_{now_ms()}_{self._task_seq}"

    async def _invoke_delegate(self, args: Dict[str, Any], *, caller: str) -> ToolExecutionResult:
        """受理委派：名册校验 → 禁自派 → 目标接收入口 → 登记记录表 → 回执。

        受理失败（本方法内返回的 failure）与任务失败（执行中写入记录表的
        failed 终态）严格分开——回执只承诺"目标已接收"，不承诺"能干成"。
        """
        target_name = str(args.get("agent", "") or "")
        instruction = str(args.get("instruction", "") or "")
        if not target_name or not instruction:
            return ToolExecutionResult(
                tool_name="framework_delegate",
                success=False,
                error_message="delegate 需要 agent（目标注册名）与 instruction（自然语言指令）",
            )
        if self.task_ledger is None:
            return ToolExecutionResult(
                tool_name="framework_delegate",
                success=False,
                error_message="任务基建未装配（task_ledger 未注入），委派不可用",
            )
        # 发起方 = 调用方（invocation.source；装配侧可按需要扩展映射）
        initiator = caller or "unknown"
        target = self.manager.get(target_name)
        if target is None:
            return ToolExecutionResult(
                tool_name="framework_delegate",
                success=False,
                error_message=f"受理失败：目标 Agent '{target_name}' 不在名册",
            )
        if target_name == initiator:
            return ToolExecutionResult(
                tool_name="framework_delegate",
                success=False,
                error_message="受理失败：不能把工作委派给自己（自派被拒）",
            )
        task_id = self._next_task_id()
        receive = getattr(target, "receive_delegation", None)
        if not callable(receive):
            return ToolExecutionResult(
                tool_name="framework_delegate",
                success=False,
                error_message=f"受理失败：目标 '{target_name}' 未实现接收入口（默认拒收）",
            )
        rejected = receive(instruction=instruction, task_id=task_id)
        if rejected:
            return ToolExecutionResult(
                tool_name="framework_delegate",
                success=False,
                error_message=f"受理失败：目标 '{target_name}' 拒收（{rejected}）",
            )
        # 受理成功：登记 agent 型任务（执行 Agent 唯一写入；循环不代写）
        self.task_ledger.register(
            task_id=task_id,
            provider="framework",
            tool="framework_delegate",
            initiator=initiator,
            executor=target_name,
            status="accepted",
            source="agent",
            snapshot={"instruction": instruction[:200]},
        )
        logger.info(f"委派受理: {initiator} -> {target_name}（task_id={task_id}）")
        return ToolExecutionResult(
            tool_name="framework_delegate",
            success=True,
            structured_content={"accepted": True, "task_id": task_id, "executor": target_name},
        )

    def _invoke_task_status(self, args: Dict[str, Any]) -> ToolExecutionResult:
        """按任务号查记录表：状态 + 快照；未知/已终态移除 → 失败结果。"""
        task_id = str(args.get("task_id", "") or "")
        if not task_id or self.task_ledger is None:
            return ToolExecutionResult(
                tool_name="framework_task_status",
                success=False,
                error_message="任务不存在（task_id 为空或任务基建未装配）",
            )
        record = self.task_ledger.get(task_id)
        if record is None:
            return ToolExecutionResult(
                tool_name="framework_task_status",
                success=False,
                error_message=f"任务不存在: '{task_id}'（未知任务号或已终态移除）",
            )
        return ToolExecutionResult(
            tool_name="framework_task_status",
            success=True,
            structured_content={
                "task_id": record.task_id,
                "status": record.status,
                "initiator": record.initiator,
                "executor": record.executor,
                "snapshot": record.snapshot,
                "updated_at_ms": record.updated_at_ms,
            },
        )


def build_agent_control_provider(
    manager: AgentManager,
    task_ledger: Optional[TaskLedger] = None,
) -> AgentControlProvider:
    """工厂：构造一个 AgentControlProvider 绑定 manager（+ 任务记录表供委派）。"""
    return AgentControlProvider(manager=manager, task_ledger=task_ledger)


__all__ = [
    "AgentControl",
    "AgentControlProvider",
    "build_agent_control_provider",
]
