"""
query_memory 工具（Wave 3 / §1.50 / §1.51 双路之第二路）

LLM 可调用 ``query_memory(query, top_k)`` 返回相关记忆；结果以
``ToolExecutionResult.content`` 文本形式呈现（最多 N 条）。

注册方式：可通过 ``default_tool_registry().register_provider(QueryMemoryToolProvider(memory))``
或全局单例 ``build_query_memory_tool()`` 提供默认空壳后绑定 memory provider。

时间字段：timestamp_ms 在 Amaidesu 内部使用毫秒（§1.53 9d），本工具不引
入秒/毫秒转换（仅在切换 AMemorixProvider 时由 Provider 内部负责）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from src.modules.logging import get_logger
from src.modules.memory.provider import MemoryProvider
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)

logger = get_logger("QueryMemoryTool")


QUERY_MEMORY_SPEC = ToolSpec(
    name="query_memory",
    description=(
        "查询长期记忆。根据 query 关键词召回 top_k 条最相关事实（默认 5 条）。"
        "返回文本格式：每条含时间戳、来源、文本内容。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "查询文本/关键词"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
        },
        "required": ["query"],
    },
    kind="sync",
    result_event="",
    provider="builtin",
)


@dataclass(slots=True)
class QueryMemoryToolProvider:
    """query_memory 工具的 ToolProvider。

    memory provider 字段可热绑：构造后修改 ``memory`` 字段以切换后端
    （默认为 ``None``，调用前必须设置）。
    """

    memory: Optional[MemoryProvider] = None

    @property
    def name(self) -> str:
        return "QueryMemoryToolProvider"

    def list_tools(self):
        yield QUERY_MEMORY_SPEC

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        if self.memory is None:
            return ToolExecutionResult(
                tool_name="query_memory",
                success=False,
                error_message="query_memory 工具未绑定 MemoryProvider",
            )
        args = invocation.arguments or {}
        query = str(args.get("query", "")).strip()
        try:
            top_k = int(args.get("top_k", 5))
        except (TypeError, ValueError):
            top_k = 5
        top_k = max(1, min(top_k, 20))

        if not query:
            return ToolExecutionResult(
                tool_name="query_memory",
                success=True,
                content="（空查询）",
            )

        try:
            hits = await self.memory.recall(query, top_k=top_k)
        except Exception as exc:  # noqa: BLE001 - 边界
            logger.error(f"query_memory recall 失败: {exc}", exc_info=True)
            return ToolExecutionResult(
                tool_name="query_memory",
                success=False,
                error_message=f"recall 失败: {type(exc).__name__}: {exc}",
            )

        return ToolExecutionResult(
            tool_name="query_memory",
            success=True,
            content=_format_hits(hits),
        )


def _format_hits(hits: List[Any]) -> str:
    """把 hits 格式化为可读文本。

    最大 50 字符文本截断防膨胀；时间戳用 ``*_ms`` int 字段（按项目惯例展示）
    """
    if not hits:
        return "（无匹配记忆）"
    lines: List[str] = []
    for idx, hit in enumerate(hits, start=1):
        text = str(getattr(hit, "text", "") or "")
        if len(text) > 80:
            text = text[:77] + "..."
        ts_ms = int(getattr(hit, "timestamp_ms", 0) or 0)
        meta = getattr(hit, "metadata", None) or {}
        src = meta.get("source", "") if isinstance(meta, dict) else ""
        lines.append(f"[{idx}] (t={ts_ms}ms, src={src}) {text}")
    return "\n".join(lines)


def build_query_memory_tool() -> QueryMemoryToolProvider:
    """工厂：构造一个空壳 ``QueryMemoryToolProvider``（待绑定 memory）。"""
    return QueryMemoryToolProvider(memory=None)


# 模块级默认 provider（被默认 ToolRegistry 引用前可绑定 memory）
_default_query_provider = build_query_memory_tool()


def get_default_query_tool() -> QueryMemoryToolProvider:
    """取得默认 query_memory provider（用于 builtin 注册）。"""
    return _default_query_provider


async def query_memory(text: str, top_k: int = 5) -> List[Any]:
    """直接函数形式（非工具路径）。供代码内调用。"""
    mem = _default_query_provider.memory
    if mem is None:
        return []
    return await mem.recall(text, top_k=top_k)


__all__ = [
    "QUERY_MEMORY_SPEC",
    "QueryMemoryToolProvider",
    "build_query_memory_tool",
    "get_default_query_tool",
    "query_memory",
]
