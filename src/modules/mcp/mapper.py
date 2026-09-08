"""MCP → Amaidesu 工具契约映射（纯函数，无副作用）

把 mcp 协议层的对象转换为项目 ToolSpec / ToolExecutionResult。
映射几乎零阻抗（探索依据）：
- ``mcp.types.Tool.name / description / inputSchema`` → ``ToolSpec`` 字段直传
- ``CallToolResult.content`` 的 TextContent / ImageContent → ``ResultBlock``
  （模型已原生支持 image(base64+mime)）
- ``structuredContent`` → ``structured_content``；``is_error`` → ``success`` 取反

设计要点：
- 纯函数：不依赖任何全局状态/kwargs 注入；便于单测，无需真实 server
- **不含 server 特定知识**：只做类型层映射，不解析任何工具语义
- 工具名**无条件**加前缀（``<server名>_``）：注册名铁定不与内置工具冲突，
  也不依赖"原名是否已带前缀"的猜测；Provider 在 list_tools 时记录
  注册名 → MCP 原名的映射表，调用时查表还原（不做任何字符串剥离）
"""

from __future__ import annotations

from typing import Any, List

from src.modules.tools.models import ResultBlock, ToolExecutionResult, ToolSpec


def normalize_tool_name(raw_name: str, prefix: str) -> str:
    """给 MCP 工具名**无条件**加前缀（防重名，不猜原名形态）。

    Args:
        raw_name: MCP 侧工具原名（无论是否已带 server 前缀，一律再加）
        prefix: 前缀（通常为 ``<server名>_``）

    Returns:
        加前缀后的注册名（如 server 名=serverA、原名=serverA_perceive
        → 注册名=serverA_serverA_perceive）

    说明：
        不做"已带前缀则跳过"的去重——那需要猜测原名形态，猜错即调用
        失败（Unknown tool）。注册名与原名的对应关系由 Provider 的
        映射表记录（见 McpToolProvider._name_map），注册名只要唯一
        且可查表还原即可，形态不重要。
    """
    name = raw_name.strip()
    if not prefix:
        return name
    return f"{prefix}{name}"


def to_spec(
    tool: Any,
    *,
    prefix: str,
    provider: str = "mcp",
) -> ToolSpec:
    """把 mcp.types.Tool 转换为 ToolSpec。

    Args:
        tool: mcp.types.Tool（有 name / description / inputSchema 属性）
        prefix: 工具名前缀（见 normalize_tool_name）
        provider: 来源标记（默认 "mcp"；内容层覆盖场景传 "game"）

    Returns:
        ToolSpec（kind="sync"：MCP 调用是请求-响应语义，无 fire-and-forget）
    """
    raw_name = getattr(tool, "name", "")
    description = getattr(tool, "description", "") or ""
    input_schema = getattr(tool, "inputSchema", None) or None

    return ToolSpec(
        name=normalize_tool_name(raw_name, prefix),
        description=description,
        parameters_schema=input_schema if isinstance(input_schema, dict) else None,
        kind="sync",
        provider=provider,
    )


def _content_to_blocks(content: Any) -> List[ResultBlock]:
    """把 CallToolResult.content（TextContent/ImageContent 等）转为 ResultBlocks。

    兼容两种形态：
    - FastMCP 3.x 的 content list（元素有 .type / .text / .data / .mimeType）
    - 兼容 dict 形态（防御性）
    """
    blocks: List[ResultBlock] = []
    if content is None:
        return blocks
    items = content if isinstance(content, (list, tuple)) else [content]

    for item in items:
        if item is None:
            continue
        try:
            if isinstance(item, dict):
                ctype = item.get("type", "text")
                if ctype == "image":
                    blocks.append(
                        ResultBlock(
                            kind="image",
                            data=str(item.get("data", "")),
                            mime_type=str(item.get("mimeType", "") or ""),
                        )
                    )
                else:
                    blocks.append(ResultBlock(kind="text", text=str(item.get("text", ""))))
                continue
            # 对象形态（mcp.types.TextContent / ImageContent）
            ctype = getattr(item, "type", "text")
            if ctype == "image":
                blocks.append(
                    ResultBlock(
                        kind="image",
                        data=str(getattr(item, "data", "") or ""),
                        mime_type=str(getattr(item, "mimeType", "") or ""),
                    )
                )
            else:
                blocks.append(ResultBlock(kind="text", text=str(getattr(item, "text", "") or "")))
        except Exception:  # noqa: BLE001 - 单个 block 解析失败不阻断整体
            blocks.append(ResultBlock(kind="text", text=str(item)))
    return blocks


def to_result(
    result: Any,
    *,
    tool_name: str,
    duration_ms: int = 0,
) -> ToolExecutionResult:
    """把 CallToolResult 转换为 ToolExecutionResult。

    Args:
        result: FastMCP CallToolResult（或有 .content / .is_error /
            .structured_content 属性的对象）；None = 调用失败
        tool_name: 注册后的完整工具名（含前缀）
        duration_ms: 已测得的调用耗时（毫秒）

    Returns:
        ToolExecutionResult（success 取 ``not is_error``；失败时 content 为错误文本）
    """
    if result is None:
        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            error_message="MCP 调用失败（连接断开或服务器错误）",
            duration_ms=duration_ms,
        )

    is_error = bool(getattr(result, "is_error", False) or False)
    structured = getattr(result, "structured_content", None)
    content = getattr(result, "content", None)
    blocks = _content_to_blocks(content)

    # 文本内容拼接（content 字段）
    text_parts: List[str] = []
    for block in blocks:
        if block.kind == "text" and block.text:
            text_parts.append(block.text)
    text_content = "\n".join(text_parts)

    if is_error:
        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            content=text_content,
            blocks=blocks,
            error_message=text_content or "MCP 服务器返回错误（无文本内容）",
            structured_content=structured,
            duration_ms=duration_ms,
        )

    return ToolExecutionResult(
        tool_name=tool_name,
        success=True,
        content=text_content,
        blocks=blocks,
        structured_content=structured,
        duration_ms=duration_ms,
    )


__all__ = [
    "normalize_tool_name",
    "to_spec",
    "to_result",
]
