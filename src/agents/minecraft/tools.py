"""MinecraftAgent 局部工具（minecraft_send_prompt / minecraft_report / minecraft_todo /
minecraft_notebook / minecraft_get_state）

工具归属约定（text_adv 同构）：
- 局部工具（minecraft_todo / minecraft_notebook）= Agent 自己 LLM 用，驱动 ReAct 循环
  ——全量读写文档，无 id
- 对外状态查询（minecraft_get_state）= 主播/外部经 ToolRegistry 调，只读——状态通道
- 对外命令（minecraft_send_prompt）= 主播/外部经 ToolRegistry 调，**纯消息投递 + 唤醒**
  ——主播是玩家 Agent 的用户（像给 Code Agent 发提示词）：派发/调整任务、回答问题、
  补充要求；系统不代写 todo（目标分解是 LLM 用 minecraft_todo 自己做的事）
- 局部上报（minecraft_report）= 玩家 Agent LLM 用：向主播交付总结（delivery）/
  升级决策（escalation）——玩家→主播唯一发声出口
- provider="minecraft"（来源溯源：提供者=游戏 Agent 名全称，禁缩写）

注册名由 ToolRegistry 自动拼接为 ``minecraft_<工具名>``（模块声明 provider，
不在工具名里手写前缀）。

全量读写设计（对齐内部 todo 工具 todowrite / edit 模式）：
- read：返回当前文档全文
- write：提交新文档全文（覆盖），无 id、无增删改查逐条 API
- LLM 拿 read 全文 → 改 → write 覆盖 = 批量修改
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional

from src.modules.logging import get_logger
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import BaseToolProvider

from .state import MinecraftAgentState

logger = get_logger("MinecraftTools")

# 提供者标识统一来源（ToolSpec.provider / 追溯用），避免字面量重复
PROVIDER_NAME = "minecraft"

# 上报回调形态：kind/content/scene 入参 → 拒绝原因（None=受理）。
# 拒绝语义（交付门禁）由 Agent 侧判定——工具层只透传结果。
ReportCallback = Callable[[str, str, str], Awaitable[Optional[str]]]


# ---------------------------------------------------------------------------
# ToolSpec 工厂
# ---------------------------------------------------------------------------


def build_todo_spec() -> ToolSpec:
    """``minecraft_todo`` 工具规格——待办文档（全量读写，驱动 ReAct 循环）"""
    return ToolSpec(
        name="todo",
        description=(
            "待办文档（全量读写，无 id）。管理 Minecraft 玩家自己的目标与进度："
            "read 返回当前 todo 全文；write 提交新全文（覆盖）。"
            "任务的分解与推进由你自己决定：把收到的上级指令分解为任务，"
            "逐项推进并标记 done。每轮决策应 read 一次最新全文。"
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


def build_notebook_spec() -> ToolSpec:
    """``minecraft_notebook`` 工具规格——工作笔记（持久记忆，LLM 自主沉淀）"""
    return ToolSpec(
        name="notebook",
        description=(
            "工作笔记文档（全量读写，无 id）。你的持久工作记忆："
            "记录值得跨轮次保留的关键信息（矿石位置/基地坐标/已完成事项/教训），"
            "对话历史会被压缩、笔记不会——重要发现写这里，每轮可根据需要读回参考。"
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
                "content": {"type": "string", "description": "write 时的新笔记全文"},
            },
            "required": ["action"],
        },
        kind="sync",
        provider=PROVIDER_NAME,
        output_schema={"type": "object", "properties": {"content": {"type": "string"}}},
    )


def build_send_prompt_spec() -> ToolSpec:
    """``minecraft_send_prompt`` 工具规格——提示词通道（主播→玩家，纯消息投递 + 唤醒）"""
    return ToolSpec(
        name="send_prompt",
        description=(
            "给玩家 Agent 发送提示词（主播→玩家通道）。"
            "用途：派发/调整任务、回答玩家的问题、补充要求。"
            "提示词原文投递给玩家，由玩家自主理解并执行（分解/推进玩家自己决定）。"
            "例：'挖 3 个钻石' / '先回基地补给，钻石稍后再挖'。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "提示词内容（目标级意图描述/回答/补充要求）"},
            },
            "required": ["content"],
        },
        kind="sync",
        provider=PROVIDER_NAME,
    )


def build_report_spec() -> ToolSpec:
    """``minecraft_report`` 工具规格——上报通道（玩家→主播，交付/升级）"""
    return ToolSpec(
        name="report",
        description=(
            "向主播上报（玩家→主播唯一发声出口）。两种："
            "kind=delivery 交付总结——任务完成时必发一次（只在全部完成时发，中途不发）；"
            "kind=escalation 升级决策——仅当确实无法自行解决（缺关键信息/需授权/"
            "资源冲突无解）时发。绝大多数困难自己解决（换路线/换策略/取消重试），"
            "不打扰主播。发完 escalation 后停止行动，静默等待主播回复。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["delivery", "escalation"],
                    "description": "delivery=交付总结；escalation=升级决策（发后停止等待）",
                },
                "content": {"type": "string", "description": "上报内容（交付总结 / 需要主播决策的事项）"},
                "scene": {"type": "string", "description": "可选场景补充（坐标/区块等上下文）"},
            },
            "required": ["kind", "content"],
        },
        kind="sync",
        provider=PROVIDER_NAME,
        output_schema={
            "type": "object",
            "properties": {
                "reported": {"type": "boolean"},
                "kind": {"type": "string"},
            },
        },
    )


def build_get_state_spec() -> ToolSpec:
    """``minecraft_get_state`` 工具规格——状态查询（状态通道，主播/外部只读调用）"""
    return ToolSpec(
        name="get_state",
        description=(
            "只读查询 Minecraft 玩家当前状态快照："
            "todo（待办与进度）/ notebook（工作笔记）/ "
            "recent_reports（近期上报：交付与升级记录）。"
            "面向直播叙事——主播不需要自己记录游戏细节，按需查询即可。"
        ),
        parameters_schema={"type": "object", "properties": {}, "required": []},
        kind="sync",
        provider=PROVIDER_NAME,
        output_schema={
            "type": "object",
            "properties": {
                "todo": {"type": "array"},
                "notebook": {"type": "string"},
                "recent_reports": {"type": "array"},
            },
        },
    )


# ---------------------------------------------------------------------------
# Provider（注册到 ToolRegistry）
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MinecraftToolProvider(BaseToolProvider):
    """MinecraftAgent 局部工具 Provider

    持有 :class:`MinecraftAgentState`，把"文档式工具"映射为状态读写；
    ``send_prompt_callback`` 由 Agent 注入——minecraft_send_prompt 提示词经它
    唤醒 Agent 任务执行（无 callback 时降级仅入队，供脱离 Agent 单测使用）。
    ``report_callback`` 由 Agent 注入——minecraft_report 上报经它发射事件并做
    交付门禁校验（返回拒绝原因字符串；None=受理）。
    """

    state: MinecraftAgentState
    send_prompt_callback: Optional[Callable[[str], Any]] = None
    report_callback: Optional[ReportCallback] = None

    @property
    def name(self) -> str:
        return "MinecraftProvider"

    def list_tools(self) -> Iterable[ToolSpec]:
        return [
            build_todo_spec(),
            build_notebook_spec(),
            build_get_state_spec(),
            build_send_prompt_spec(),
            build_report_spec(),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        started_ms = int(time.time() * 1000)
        tool_name = invocation.tool_name
        args = dict(invocation.arguments or {})

        # 注册名（minecraft_<name>）剥为声明名（<name>）：registry 分发到达的是注册名
        if tool_name.startswith(f"{PROVIDER_NAME}_"):
            tool_name = tool_name[len(f"{PROVIDER_NAME}_") :]

        try:
            if tool_name == "todo":
                result = await self._invoke_todo(args)
            elif tool_name == "notebook":
                result = await self._invoke_notebook(args)
            elif tool_name == "get_state":
                result = await self._invoke_get_state(args)
            elif tool_name == "send_prompt":
                result = await self._invoke_send_prompt(args)
            elif tool_name == "report":
                result = await self._invoke_report(args)
            else:
                return ToolExecutionResult(
                    tool_name=tool_name,
                    success=False,
                    error_message=(
                        f"未知工具: '{tool_name}'"
                        "（MinecraftProvider 只提供 todo/notebook/get_state/send_prompt/report）"
                    ),
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
            return {"tool": "todo", **self.state.todo_doc()}
        if action == "write":
            todos = args.get("todos")
            if not isinstance(todos, list):
                return {"success": False, "error": "write 需要 todos 列表"}
            self.state.set_todos(todos)
            return {"success": True, "todos": self.state.todo_doc()["todos"]}
        return {"success": False, "error": f"未知 action: {action!r}"}

    async def _invoke_notebook(self, args: Dict[str, Any]) -> Dict[str, Any]:
        action = str(args.get("action", ""))
        if action == "read":
            return {"tool": "notebook", **self.state.notebook_doc()}
        if action == "write":
            content = str(args.get("content", ""))
            self.state.set_notebook(content)
            return {"success": True, "content": self.state.notebook}
        return {"success": False, "error": f"未知 action: {action!r}"}

    async def _invoke_get_state(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"tool": "get_state", **self.state.to_dict()}

    async def _invoke_send_prompt(self, args: Dict[str, Any]) -> Dict[str, Any]:
        content = str(args.get("content", ""))
        if not content:
            return {"success": False, "error": "send_prompt 需要 content 字符串"}
        if self.send_prompt_callback is not None:
            result = self.send_prompt_callback(content)
            if hasattr(result, "__await__"):
                await result
        else:
            # 降级仅记录（无 Agent 注入时消息不丢，但不会触发任务执行）
            logger.warning("minecraft_send_prompt 无 send_prompt_callback：消息未投递")
        return {"success": True, "delivered": True}

    async def _invoke_report(self, args: Dict[str, Any]) -> Dict[str, Any]:
        kind = str(args.get("kind", ""))
        if kind not in ("delivery", "escalation"):
            return {"success": False, "error": "kind 必须是 delivery 或 escalation"}
        content = str(args.get("content", "")).strip()
        if not content:
            return {"success": False, "error": "report 需要 content 字符串"}
        scene = str(args.get("scene", ""))
        if self.report_callback is not None:
            rejection = await self.report_callback(kind, content, scene)
            if rejection:
                return {"success": False, "error": rejection, "kind": kind}
        else:
            # 降级仅记录（无 Agent 注入时上报不发射事件，供脱离 Agent 单测使用）
            logger.warning("minecraft_report 无 report_callback：上报未发射事件")
        return {"success": True, "reported": True, "kind": kind}


__all__ = [
    "MinecraftToolProvider",
    "build_todo_spec",
    "build_notebook_spec",
    "build_get_state_spec",
    "build_send_prompt_spec",
    "build_report_spec",
    "PROVIDER_NAME",
    "ReportCallback",
]
