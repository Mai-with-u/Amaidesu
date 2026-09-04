"""command_tool - 主播命令解析工具

**真工具**——通过 ``ToolProvider`` 注册到 ToolRegistry，供 LLM 调用。
底层执行器是 ``CommandParser`` + ``CommandRegistry``（**不**注册为工具；
这是纯解析层）。

设计要点：
- 原 ``CommandDecider``（订阅 input.message.received 自动跑）→ REWRITE 为 Tool
  （LLM 显式调用：用户发命令 → Planner 检测到 → 调 parse_command 工具）
- 命令执行（action 路由）：原 CommandDecider 只生成 Intent 然后 publish 事件；
  新架构下命令执行通过调用对应 action 工具完成（如 attack 调 vts 工具）

工具契约：
- kind: ``"sync"``（纯解析立即返回）
- provider: ``"builtin"``（框架内置）
- arguments: ``{text}``（待解析的命令文本）
- 返回：ToolExecutionResult(content=JSON dict {is_command, name, args, raw_args, action})
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from src.modules.logging import get_logger
from src.modules.tools import ToolInvocation, ToolSpec
from src.modules.tools.models import ToolExecutionResult
from src.modules.types.base.normalized_message import NormalizedMessage

from ..command.command_parser import CommandParser
from ..command.command_registry import CommandRegistry

__all__ = ["CommandToolProvider", "build_command_tool_spec", "register_command_tool"]


_COMMAND_TOOL_NAME = "parse_command"
_COMMAND_TOOL_DESCRIPTION = (
    "解析用户命令（以 / 开头的文本）。返回 {is_command, name, args, raw_args, action}。"
    "非命令文本 → is_command=false。命令 → name + args + 注册表映射的 action。"
    "本工具只解析，**不**执行命令动作；执行由 Planner 根据 action 字段调对应工具完成。"
)


_COMMAND_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "待解析的命令文本（弹幕 text 字段）",
        },
    },
    "required": ["text"],
}


def build_command_tool_spec() -> ToolSpec:
    """构造 parse_command 工具的 ToolSpec。"""
    return ToolSpec(
        name=_COMMAND_TOOL_NAME,
        description=_COMMAND_TOOL_DESCRIPTION,
        parameters_schema=_COMMAND_PARAMETERS_SCHEMA,
        kind="sync",
        provider="builtin",
    )


class CommandToolProvider:
    """parse_command 工具的 Provider（满足 ``ToolProvider`` 协议）。

    StreamerAgent 直接 ``registry.register_provider(provider)`` 注册。
    invoke 时按 ``invocation.tool_name == "parse_command"`` 分发。
    """

    def __init__(
        self,
        *,
        parser: Optional[CommandParser] = None,
        registry: Optional[CommandRegistry] = None,
        command_prefix: str = "/",
        command_mappings: Optional[dict[str, str]] = None,
    ) -> None:
        """初始化。

        Args:
            parser: ``CommandParser`` 实例（None 时用 command_prefix 新建）
            registry: ``CommandRegistry`` 实例（None 时新建 + 装载 command_mappings）
            command_prefix: 命令前缀（如 ``"/"``）
            command_mappings: 初始命令映射 {name: action}
        """
        self._parser = parser or CommandParser(command_prefix=command_prefix)
        if registry is None:
            registry = CommandRegistry()
            if command_mappings:
                registry.load_from_config(command_mappings)
        self._registry = registry
        self._logger = get_logger("CommandTool")

    @property
    def name(self) -> str:
        return "CommandTool"

    def list_tools(self) -> Iterable[ToolSpec]:
        return [build_command_tool_spec()]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """parse_command 工具的 invoke。"""
        if invocation.tool_name != _COMMAND_TOOL_NAME:
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=f"CommandToolProvider 不处理工具 '{invocation.tool_name}'",
            )

        args = invocation.arguments or {}
        text = str(args.get("text", "") or "")
        if not text.strip():
            return ToolExecutionResult(
                tool_name=_COMMAND_TOOL_NAME,
                success=True,
                content=json.dumps(
                    {"is_command": False, "reason": "empty text"},
                    ensure_ascii=False,
                ),
            )

        if not self._parser.is_command(text):
            return ToolExecutionResult(
                tool_name=_COMMAND_TOOL_NAME,
                success=True,
                content=json.dumps(
                    {"is_command": False, "text": text},
                    ensure_ascii=False,
                ),
            )

        # 用一个 dummy NormalizedMessage 满足 parser 接口
        dummy_msg = NormalizedMessage(text=text, source="command_tool")
        cmd = self._parser.parse_command(text, dummy_msg)
        if cmd is None:
            return ToolExecutionResult(
                tool_name=_COMMAND_TOOL_NAME,
                success=True,
                content=json.dumps(
                    {"is_command": False, "text": text},
                    ensure_ascii=False,
                ),
            )

        is_supported = self._registry.is_supported_command(cmd.name)
        action = self._registry.get_action(cmd.name) if is_supported else None

        result_dict = {
            "is_command": True,
            "name": cmd.name,
            "args": cmd.args,
            "raw_args": cmd.raw_args,
            "supported": is_supported,
            "action": action,
        }
        return ToolExecutionResult(
            tool_name=_COMMAND_TOOL_NAME,
            success=True,
            content=json.dumps(result_dict, ensure_ascii=False),
        )


def register_command_tool(
    registry: Any,
    *,
    parser: Optional[CommandParser] = None,
    cmd_registry: Optional[CommandRegistry] = None,
    command_prefix: str = "/",
    command_mappings: Optional[dict[str, str]] = None,
) -> bool:
    """便捷函数：构造 parse_command 工具 Provider 并注册到 ToolRegistry。"""
    provider = CommandToolProvider(
        parser=parser,
        registry=cmd_registry,
        command_prefix=command_prefix,
        command_mappings=command_mappings,
    )
    return registry.register_provider(provider)
