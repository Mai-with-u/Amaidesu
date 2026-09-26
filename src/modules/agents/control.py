"""
AgentControl —— 框架级 Agent 控制与委派/递话

- framework_delegate / framework_task_status：跨 Agent 委派原语
  ——派活拿回执（accepted + task_id），任务进度随时可查；指令只当自然
  语言（给目标，不给步骤），不加编排/条件分支
- framework_prompt：递话原语——纯文本留言（插话/提醒），不派任务、
  不进账本；与委派共享 Agent 侧文本接缝（receive_prompt），记账分家
- provider="framework"（框架内置提供，非独立源；可见名单默认 ["*"]）
- pause / resume / shutdown / 状态查询等控制能力由 ``AgentControl``
  类本体承载，不进 LLM 工具面；控制面（DashboardServer）经 API 直调
  （重建走 ``AgentManager.rebuild``，不经本类）

LLM 工具面注册方式（framework provider 含 delegate/prompt/task_status 三个 spec）：
```python
provider = build_agent_control_provider(manager, task_ledger)
tool_registry.register_provider(provider)
```
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Iterable, List, Optional

from src.modules.agents.manager import AgentManager
from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.tasks import TaskLedger
from src.modules.time_utils import now_ms

logger = get_logger("AgentControl")


# -------------------- 工具规格 --------------------

#: 委派工具的固定说明（可委派对象随名册动态追加，见 AgentControlProvider.list_tools）
_DELEGATE_DESCRIPTION = (
    "把一项工作委派给目标 Agent：给目标与自然语言指令（不给步骤），"
    "立刻返回受理回执（accepted + task_id）。任务状态变化会以事件通知你；"
    "随时可用 framework_task_status 按 task_id 查询进度与快照。"
    "目标忙时会排队，无须等待。"
)

#: 递话工具的固定说明
_PROMPT_DESCRIPTION = (
    "向目标 Agent 递话（插话/提醒/纠正）：纯文本留言，不派新任务、不产生"
    "任务号。目标执行中会在下一步推理前吸收，任务挂起中会被唤醒重新判断。"
)

#: 任务状态工具规格
_TASK_STATUS_SPEC: ToolSpec = ToolSpec(
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
)

#: 递话工具规格（agent 参数说明随名册动态构造，见 _prompt_spec）
_PROMPT_SPEC: ToolSpec = ToolSpec(
    name="prompt",
    description=_PROMPT_DESCRIPTION,
    parameters_schema={
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "目标 Agent 注册名"},
            "content": {"type": "string", "description": "递话内容（自然语言：想说给目标的话）"},
        },
        "required": ["content"],
    },
    kind="sync",
    provider="framework",
)


# -------------------- AgentControl 工具（写形式，可直接 await） --------------------

# 任务号序号源（模块级共享：provider 与控制面直调同一序列，避免同毫秒撞号）
_task_id_seq = 0


def _generate_task_id() -> str:
    """生成委派任务号（deleg_{epoch_ms}_{seq}，全链路关联键）。"""
    global _task_id_seq
    _task_id_seq += 1
    return f"deleg_{now_ms()}_{_task_id_seq}"


class AgentControl:
    """框架级控制——直接调用接口（不进 ToolRegistry）。"""

    def __init__(self, manager: AgentManager, task_ledger: Optional[TaskLedger] = None) -> None:
        self._manager = manager
        self._task_ledger = task_ledger

    async def pause(self, name: str) -> bool:
        agent = self._manager.get_agent_by_name(name)
        if agent is None:
            logger.warning(f"pause_agent: 未找到 Agent '{name}'")
            return False
        await agent.pause()
        return True

    async def resume(self, name: str) -> bool:
        agent = self._manager.get_agent_by_name(name)
        if agent is None:
            logger.warning(f"resume_agent: 未找到 Agent '{name}'")
            return False
        await agent.resume()
        return True

    async def shutdown(self, name: str) -> bool:
        agent = self._manager.get_agent_by_name(name)
        if agent is None:
            logger.warning(f"shutdown_agent: 未找到 Agent '{name}'")
            return False
        await agent.shutdown()
        return True

    async def prompt(self, name: str, content: str, *, source: str = "operator") -> dict:
        """向指定 Agent 递话（控制面直调，供运营 REST）。

        返回 ``{"ok": True, "delivered": True, "executor": name}`` 或
        ``{"ok": False, "error": "not_found"|"refused", "message": …}``——
        两种失败语义分开，REST 侧据此映射 404 / 409。
        """
        agent = self._manager.get_agent_by_name(name)
        if agent is None:
            logger.warning(f"prompt: 未找到 Agent '{name}'")
            return {"ok": False, "error": "not_found", "message": f"Agent 不存在: {name}"}
        delivered = agent.receive_prompt(content=content, source=source)
        if not delivered:
            logger.info(f"prompt: Agent '{name}' 拒收递话（source={source}）")
            return {"ok": False, "error": "refused", "message": f"Agent '{name}' 拒收递话（未实现消化通道或留言已满）"}
        return {"ok": True, "delivered": True, "executor": name}

    async def cancel_task(self, name: str, task_id: str, *, source: str = "operator") -> dict:
        """硬取消指定 Agent 的任务（控制面直调，供运营 REST）。

        取消经执行 Agent（单写者规则：框架不代写账面）。返回
        ``{"ok": True, "cancelled": True}`` 或
        ``{"ok": False, "error": "agent_not_found"|"task_not_found",
        "message": …}``——REST 侧一律映射 404（不在名册 / 任务未知或已终态）。
        """
        agent = self._manager.get_agent_by_name(name)
        if agent is None:
            logger.warning(f"cancel_task: 未找到 Agent '{name}'")
            return {"ok": False, "error": "agent_not_found", "message": f"Agent 不存在: {name}"}
        cancelled = agent.cancel_task(task_id, source=source)
        if not cancelled:
            logger.info(f"cancel_task: Agent '{name}' 拒收取消（task_id={task_id}）")
            return {
                "ok": False,
                "error": "task_not_found",
                "message": f"任务不存在或已终态: {task_id}（Agent '{name}' 未在追踪）",
            }
        return {"ok": True, "cancelled": True}

    async def delegate(self, name: str, instruction: str, *, initiator: str = "operator") -> dict:
        """运营直派任务（控制面直调，供 REST）：登记账本 + 目标接收入口。

        与 LLM 工具委派同一张任务记录表（source="agent"），发起方记
        ``initiator``（REST 侧固定 "operator"）。任务号先生成再交目标
        接收（与 provider 同序：拒收则不登记，不留孤儿 accepted 条目）。
        """
        agent = self._manager.get_agent_by_name(name)
        if agent is None:
            logger.warning(f"delegate: 未找到 Agent '{name}'")
            return {"ok": False, "error": "not_found", "message": f"Agent 不存在: {name}"}
        if self._task_ledger is None:
            return {"ok": False, "error": "no_ledger", "message": "任务基建未装配（task_ledger 未注入），委派不可用"}
        task_id = _generate_task_id()
        rejected = agent.receive_delegation(instruction=instruction, task_id=task_id)
        if rejected:
            logger.info(f"delegate: Agent '{name}' 拒收委派（{rejected}）")
            return {"ok": False, "error": "refused", "message": f"目标 '{name}' 拒收（{rejected}）"}
        self._task_ledger.register(
            task_id=task_id,
            provider="framework",
            tool="framework_delegate",
            initiator=initiator,
            executor=name,
            status="accepted",
            source="agent",
            snapshot={"instruction": instruction[:200]},
        )
        logger.info(f"运营直派受理: {initiator} -> {name}（task_id={task_id}）")
        return {"ok": True, "accepted": True, "task_id": task_id, "executor": name}

    def list_agents(self) -> List[str]:
        return self._manager.list_agents()

    def state_of(self, name: str) -> Optional[dict]:
        agent = self._manager.get_agent_by_name(name)
        if agent is None:
            return None
        # is_alive 走 manager 的守护阈值（config 化）；能走到这里说明 agent 在名册，不会是 None
        alive = self._manager.is_agent_alive(name)
        return {
            "name": agent.name,
            "state": agent.state.value,
            "heartbeat_ms": agent.heartbeat.last_heartbeat_ms,
            "is_alive": alive if alive is not None else agent.is_alive(),
            "restart_count": agent.restart_count,
        }


# -------------------- Provider：用于注册进 ToolRegistry --------------------


@dataclass(slots=True)
class AgentControlProvider(BaseToolProvider):
    """把 framework LLM 工具面（委派/递话/任务状态）注册到 ToolRegistry 的 Provider。

    控制工具（pause/resume/shutdown/restart/list/state）不在本 provider 中——
    控制面由 DashboardServer 经 API 直调 ``AgentControl`` 类本体。

    委派原语：
    - ``framework_delegate(agent, instruction)``：名册校验 + **禁自派** +
      目标接收入口（BaseAgent 默认拒收）→ 受理成功登记同一张任务记录表
      （agent 型：发起方=调用方、执行者=目标、事实源=执行 Agent）
    - 受理失败（目标不存在/未启用/拒收/自派）与任务失败（执行中失败）分开
    - ``framework_task_status(task_id)``：查记录表；未知任务号 → 失败结果

    递话原语（与委派并列：传输同路、记账分家——纯文本留言不进账本）：
    - ``framework_prompt(agent, content)``：目标解析与禁自派复用委派同款
      逻辑；调 ``target.receive_prompt``，拒收 → 失败结果、受理 → 回执
      （无任务号）
    """

    manager: AgentManager
    task_ledger: Optional[TaskLedger] = None

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category: ClassVar[str] = "framework"

    @property
    def name(self) -> str:
        return "framework"

    def list_tools(self) -> Iterable[ToolSpec]:
        """委派 + 递话 + 任务状态工具。

        **可委派对象取自当前名册**（AgentManager 注册表）：主 Agent 不写死任何
        游戏名——接的是哪个游戏由配置里的 enabled 名单决定，换游戏只需换 Agent，
        提示词与框架零改动。名册每个决策窗重新拉取，随启停实时变化。
        """
        return [self._delegate_spec(), _PROMPT_SPEC, _TASK_STATUS_SPEC]

    def _roster(self) -> Dict[str, str]:
        """当前名册 {注册名: 描述}（读不到时按空名册）。"""
        roster: Dict[str, str] = {}
        list_agents = getattr(self.manager, "list_agents", None)
        descriptions = getattr(self.manager, "descriptions", {}) or {}
        if callable(list_agents):
            roster = {name: str(descriptions.get(name, "") or "") for name in list_agents()}
        return roster

    def _agent_target_description(self) -> str:
        """构造 agent 参数的动态说明（名册 + 描述；委派/递话共用）。"""
        roster = self._roster()
        if roster:
            listed = "；".join(f"{name}={desc or '（无描述）'}" for name, desc in roster.items())
            return (
                f"目标 Agent 注册名（当前可委派：{listed}；留空 = 当前唯一启用的游戏 Agent，"
                "有多个候选时必须点名；不能指定自己）"
            )
        return "目标 Agent 注册名（当前名册为空，省略即派给当前唯一启用的游戏 Agent）"

    def _delegate_spec(self) -> ToolSpec:
        """构造委派规格：把名册（注册名 + 描述）写进 agent 参数说明与枚举。"""
        roster = self._roster()
        schema: Dict[str, Any] = {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": self._agent_target_description()},
                "instruction": {
                    "type": "string",
                    "description": "工作指令（自然语言：目标与约束，不规定步骤与次序）",
                },
            },
            "required": ["instruction"],
        }
        if roster:
            schema["properties"]["agent"]["enum"] = sorted(roster)
        return ToolSpec(
            name="delegate",
            description=_DELEGATE_DESCRIPTION,
            parameters_schema=schema,
            kind="sync",
            provider="framework",
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        args = invocation.arguments or {}
        name = invocation.tool_name
        # 调用方使用的就是派生全名（framework_delegate 等），等值对照分发
        try:
            if name == "framework_delegate":
                return await self._invoke_delegate(args, caller=invocation.source)
            if name == "framework_prompt":
                return self._invoke_prompt(args, caller=invocation.source)
            if name == "framework_task_status":
                return self._invoke_task_status(args)
            return ToolExecutionResult(
                tool_name=name,
                success=False,
                error_message=f"未知 AgentControl 工具 '{name}'",
            )
        except Exception as exc:  # noqa: BLE001 - 边界兜底
            logger.exception(f"AgentControl 工具 '{name}' 执行失败: {exc}")
            return ToolExecutionResult(
                tool_name=name,
                success=False,
                error_message=f"{type(exc).__name__}: {exc}",
            )

    # ----- 委派原语（framework_delegate / framework_task_status） -----

    def _next_task_id(self) -> str:
        """生成委派任务号（deleg_{epoch_ms}_{seq}，全链路关联键）。"""
        return _generate_task_id()

    def _resolve_target(
        self, target_name: str, caller: str, tool_name: str
    ) -> tuple[Optional[Any], Optional[ToolExecutionResult]]:
        """解析目标 Agent（委派/递话共用）：留空兜底 + 名册校验 + 禁自派。

        留空目标按**当前唯一启用的游戏 Agent** 解析：接的是哪个游戏由配置的
        enabled 名单决定，这里不写死任何游戏名；候选不唯一就拒绝并要求点名，
        避免把话递错游戏。解析失败返回 ``(None, failure)``。
        """
        if not target_name:
            candidates = [name for name in self.manager.list_agents() if name != caller]
            if len(candidates) != 1:
                detail = "当前没有其他已启用的 Agent" if not candidates else f"当前启用多个（{'、'.join(candidates)}）"
                return None, ToolExecutionResult(
                    tool_name=tool_name,
                    success=False,
                    error_message=f"受理失败：未指定目标，且{detail}——请点名目标注册名",
                )
            target_name = candidates[0]
        initiator = caller or "unknown"
        if target_name == initiator:
            return None, ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error_message="受理失败：目标不能是自己（自派被拒）",
            )
        target = self.manager.get_agent_by_name(target_name)
        if target is None:
            return None, ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error_message=f"受理失败：目标 Agent '{target_name}' 不在名册",
            )
        return target, None

    async def _invoke_delegate(self, args: Dict[str, Any], *, caller: str) -> ToolExecutionResult:
        """受理委派：目标解析 → 目标接收入口 → 登记记录表 → 回执。

        受理失败（本方法内返回的 failure）与任务失败（执行中写入记录表的
        failed 终态）严格分开——回执只承诺"目标已接收"，不承诺"能干成"。
        """
        target_name = str(args.get("agent", "") or "")
        instruction = str(args.get("instruction", "") or "")
        if not instruction:
            return ToolExecutionResult(
                tool_name="framework_delegate",
                success=False,
                error_message="delegate 需要 instruction（自然语言指令）",
            )
        if self.task_ledger is None:
            return ToolExecutionResult(
                tool_name="framework_delegate",
                success=False,
                error_message="任务基建未装配（task_ledger 未注入），委派不可用",
            )
        # 发起方 = 调用方（invocation.source；装配侧可按需要扩展映射）
        initiator = caller or "unknown"
        target, failure = self._resolve_target(target_name, caller, "framework_delegate")
        if failure is not None:
            return failure
        assert target is not None  # 解析成功时必有实例（类型收窄）
        target_name = target.name  # 留空目标时由 helper 解析出注册名
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

    def _invoke_prompt(self, args: Dict[str, Any], *, caller: str) -> ToolExecutionResult:
        """受理递话：目标解析（复用委派逻辑）→ 目标接收入口 → 回执。

        纯文本留言**不登记任务记录表**——递话与委派传输同路、记账分家。
        """
        content = str(args.get("content", "") or "")
        if not content:
            return ToolExecutionResult(
                tool_name="framework_prompt",
                success=False,
                error_message="prompt 需要 content（递话文本）",
            )
        target_name = str(args.get("agent", "") or "")
        target, failure = self._resolve_target(target_name, caller, "framework_prompt")
        if failure is not None:
            return failure
        assert target is not None  # 解析成功时必有实例（类型收窄）
        delivered = target.receive_prompt(content=content, source=caller or "unknown")
        if not delivered:
            return ToolExecutionResult(
                tool_name="framework_prompt",
                success=False,
                error_message=f"目标 '{target.name}' 拒收递话（未实现消化通道或留言已满）",
            )
        return ToolExecutionResult(
            tool_name="framework_prompt",
            success=True,
            structured_content={"delivered": True, "executor": target.name},
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
