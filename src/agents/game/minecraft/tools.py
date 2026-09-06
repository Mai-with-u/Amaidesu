"""MinecraftAgent 局部工具（mc_todo / mc_memo / mc_get_state）

工具归属约定（text_adv 同构）：
- 局部工具（mc_todo / mc_memo）= Agent 自己 LLM 用，驱动决策循环——全量读写文档，无 id
- 对外状态查询（mc_get_state）= 主播/外部经 ToolRegistry 调，只读——状态通道
- provider="game"（来源溯源：游戏 Agent 声明的工具）

全量读写设计（对齐内部 todo 工具 todowrite / edit 模式）：
- read：返回当前文档全文
- write：提交新文档全文（覆盖），无 id、无增删改查逐条 API
- LLM 拿 read 全文 → 改 → write 覆盖 = 批量修改
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable

from src.modules.logging import get_logger
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import ToolProvider

from .state import MinecraftAgentState

logger = get_logger("MinecraftTools")

# 提供者标识统一来源（ToolSpec.provider / 追溯用），避免字面量重复
PROVIDER_NAME = "game"


# ---------------------------------------------------------------------------
# ToolSpec 工厂
# ---------------------------------------------------------------------------


def build_todo_spec() -> ToolSpec:
    """``mc_todo`` 工具规格——待办文档（全量读写，驱动决策循环）"""
    return ToolSpec(
        name="mc_todo",
        description=(
            "待办文档（全量读写，无 id）。管理 Minecraft 玩家的目标与进度："
            "read 返回当前 todo 全文；write 提交新全文（覆盖）。"
            "记录 set_goal 收到的上级指令分解出的任务，以及自己的执行进度。"
            "每轮决策循环应 read 一次。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write"],
                    "description": "read=返回全文；write=全量覆盖",
                },
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "任务内容"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "done"],
                                "description": "任务状态",
                            },
                        },
                        "required": ["content"],
                    },
                    "description": "write 时的新 todo 全文（覆盖旧文档）",
                },
            },
            "required": ["action"],
        },
        kind="sync",
        provider=PROVIDER_NAME,
        output_schema={
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {"type": "string"},
                        },
                    },
                }
            },
        },
    )


def build_memo_spec() -> ToolSpec:
    """``mc_memo`` 工具规格——备忘录文档（LLM 自主沉淀，按需读写）"""
    return ToolSpec(
        name="mc_memo",
        description=(
            "备忘录文档（全量读写，无 id）。记录值得向主播/直播叙事转述的关键信息"
            "（发现/进展/事件）；按可转述性筛选，非无关琐碎数据。"
            "read 返回全文；write 提交新全文（覆盖）。按需使用，不每轮强制。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write"],
                    "description": "read=返回全文；write=全量覆盖",
                },
                "content": {"type": "string", "description": "write 时的新备忘录全文"},
            },
            "required": ["action"],
        },
        kind="sync",
        provider=PROVIDER_NAME,
        output_schema={"type": "object", "properties": {"content": {"type": "string"}}},
    )


def build_set_goal_spec() -> ToolSpec:
    """``mc_set_goal`` 工具规格——命令通道（主播→游戏，目标级，只读语义）"""
    return ToolSpec(
        name="mc_set_goal",
        description=(
            "给 Minecraft 玩家下达目标级指令（主播→游戏命令，不可拒绝）。"
            "目标写入玩家待办/当前目标，由玩家自主执行（怎么干玩家自己定）。"
            "例：'挖 3 个钻石' / '在基地南边建一座房子'。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "目标级意图描述"},
            },
            "required": ["goal"],
        },
        kind="sync",
        provider=PROVIDER_NAME,
    )


def build_get_state_spec() -> ToolSpec:
    """``mc_get_state`` 工具规格——状态查询（状态通道，主播/外部只读调用）"""
    return ToolSpec(
        name="mc_get_state",
        description=(
            "只读查询 Minecraft 玩家当前状态快照："
            "current_goal（当前目标）/ todo（待办与进度）/ memo（关键发现）/ "
            "recent_milestones（近期里程碑）。"
            "面向直播叙事——主播不需要自己记录游戏细节，按需查询即可。"
        ),
        parameters_schema={"type": "object", "properties": {}, "required": []},
        kind="sync",
        provider=PROVIDER_NAME,
        output_schema={
            "type": "object",
            "properties": {
                "current_goal": {"type": "string"},
                "todo": {"type": "array"},
                "memo": {"type": "string"},
                "recent_milestones": {"type": "array"},
            },
        },
    )


# ---------------------------------------------------------------------------
# Provider（注册到 ToolRegistry）
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MinecraftToolProvider(ToolProvider):
    """MinecraftAgent 局部工具 Provider

    持有 :class:`MinecraftAgentState`，把"文档式工具"映射为状态读写。
    """

    state: MinecraftAgentState

    @property
    def name(self) -> str:
        return "MinecraftProvider"

    def list_tools(self) -> Iterable[ToolSpec]:
        return [
            build_todo_spec(),
            build_memo_spec(),
            build_get_state_spec(),
            build_set_goal_spec(),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        started_ms = int(time.time() * 1000)
        tool_name = invocation.tool_name
        args = dict(invocation.arguments or {})

        try:
            if tool_name == "mc_todo":
                result = await self._invoke_todo(args)
            elif tool_name == "mc_memo":
                result = await self._invoke_memo(args)
            elif tool_name == "mc_get_state":
                result = await self._invoke_get_state(args)
            elif tool_name == "mc_set_goal":
                result = await self._invoke_set_goal(args)
            else:
                return ToolExecutionResult(
                    tool_name=tool_name,
                    success=False,
                    error_message=f"未知工具: '{tool_name}'（MinecraftProvider 只提供 mc_todo/mc_memo/mc_get_state/mc_set_goal）",
                    duration_ms=int(time.time() * 1000) - started_ms,
                )
        except Exception as exc:  # noqa: BLE001 - 工具边界兜底
            logger.error(f"Minecraft 工具 '{tool_name}' 执行失败: {exc}", exc_info=True)
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error_message=f"{type(exc).__name__}: {exc}",
                duration_ms=int(time.time() * 1000) - started_ms,
            )

        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            structured_content=result,
            duration_ms=int(time.time() * 1000) - started_ms,
        )

    async def _invoke_todo(self, args: Dict[str, Any]) -> Dict[str, Any]:
        action = str(args.get("action", ""))
        if action == "read":
            return {"tool": "mc_todo", **self.state.todo_doc()}
        if action == "write":
            todos = args.get("todos")
            if not isinstance(todos, list):
                return {"success": False, "error": "write 需要 todos 列表"}
            self.state.set_todos(todos)
            return {"success": True, "todos": self.state.todo_doc()["todos"]}
        return {"success": False, "error": f"未知 action: {action!r}"}

    async def _invoke_memo(self, args: Dict[str, Any]) -> Dict[str, Any]:
        action = str(args.get("action", ""))
        if action == "read":
            return {"tool": "mc_memo", **self.state.memo_doc()}
        if action == "write":
            content = str(args.get("content", ""))
            self.state.set_memo(content)
            return {"success": True, "content": self.state.memo}
        return {"success": False, "error": f"未知 action: {action!r}"}

    async def _invoke_get_state(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"tool": "mc_get_state", **self.state.to_dict()}

    async def _invoke_set_goal(self, args: Dict[str, Any]) -> Dict[str, Any]:
        goal = str(args.get("goal", ""))
        if not goal:
            return {"success": False, "error": "set_goal 需要 goal 字符串"}
        self.state.set_goal(goal)
        return {"success": True, "current_goal": self.state.current_goal}


__all__ = [
    "MinecraftToolProvider",
    "build_todo_spec",
    "build_memo_spec",
    "build_get_state_spec",
    "build_set_goal_spec",
]
