"""MinecraftAgent 局部工具（minecraft_report / minecraft_todo /
minecraft_notebook / minecraft_get_work_log）

工具归属约定（text_adv 同构）：
- 局部工具（minecraft_todo / minecraft_notebook）= Agent 自己 LLM 用，驱动 ReAct 循环
  ——全量读写文档，无 id
- 对外工作文档读取（minecraft_get_work_log）= 主播经 ToolRegistry 调，只读——叙事素材通道
- 跨 Agent 派活走框架委派原语（framework_delegate；原 minecraft_send_prompt
  已退役，职能并入 Agent 的接收委派入口）——主播是玩家 Agent 的用户：
  派发/调整任务、回答问题、补充要求；系统不代写 todo（目标分解是 LLM 用
  minecraft_todo 自己做的事）
- 局部上报（minecraft_report）= 玩家 Agent LLM 用：向主播交付总结（delivery）/
  升级决策（escalation）——玩家→主播唯一发声出口
- provider="minecraft"（来源溯源：提供者=游戏 Agent 名全称，禁缩写）

对外全名由 ``ToolSpec.full_name`` 派生（``minecraft_<工具名>``，provider 即模块名），
工具名里不手写前缀。

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


def build_get_work_log_spec() -> ToolSpec:
    """``minecraft_get_work_log`` 工具规格——工作文档读服务（状态通道，主播只读调用）"""
    return ToolSpec(
        name="get_work_log",
        description=(
            "只读查询 Minecraft 玩家的工作文档："
            "todo（待办与进度）/ notebook（工作笔记）/ "
            "recent_reports（近期上报：交付与升级记录）。"
            "面向直播叙事——主播不需要自己记录游戏细节，按需查询即可。"
            "本工具不查异步任务记录表；查任务进度用 framework_task_status。"
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
    ``report_callback`` 由 Agent 注入——minecraft_report 上报经它发射事件并做
    交付门禁校验（返回拒绝原因字符串；None=受理）。
    """

    state: MinecraftAgentState
    report_callback: Optional[ReportCallback] = None

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    def list_tools(self) -> Iterable[ToolSpec]:
        return [
            build_todo_spec(),
            build_notebook_spec(),
            build_get_work_log_spec(),
            build_report_spec(),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        started_ms = int(time.time() * 1000)
        tool_name = invocation.tool_name
        args = dict(invocation.arguments or {})

        # 调用方使用的就是派生全名（minecraft_todo 等），按 spec 全名等值对照分发
        local_specs = self.list_tools()
        matched = next((s for s in local_specs if s.full_name == tool_name), None)
        if matched is None:
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error_message=(
                    f"未知工具: '{tool_name}'（MinecraftProvider 只提供 todo/notebook/get_work_log/report）"
                ),
                duration_ms=int(time.time() * 1000) - started_ms,
            )

        try:
            if matched.name == "todo":
                result = await self._invoke_todo(args)
            elif matched.name == "notebook":
                result = await self._invoke_notebook(args)
            elif matched.name == "get_work_log":
                result = await self._invoke_get_work_log(args)
            elif matched.name == "report":
                result = await self._invoke_report(args)
            else:
                return ToolExecutionResult(
                    tool_name=tool_name,
                    success=False,
                    error_message=f"未知工具: '{tool_name}'",
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

    async def _invoke_get_work_log(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"tool": "get_work_log", **self.state.to_dict()}

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
    "build_get_work_log_spec",
    "build_report_spec",
    "PROVIDER_NAME",
    "ReportCallback",
]
