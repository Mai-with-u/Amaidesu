"""
query_memory 工具

LLM 可调用 ``query_memory(query, top_k)`` 返回相关记忆；结果以
``ToolExecutionResult.content`` 文本形式呈现（最多 N 条）。

**简单工具正典路径样板**——本文件演示无状态简单工具的标准写法：
``ToolSpec`` + 普通 async 函数（经 ``as_tool_impl`` 包装返回值/异常/计时）
+ ``make_provider_from_specs`` 组装 Provider，装配处（组合根经
``bind_memory_tools``）一行 ``register_provider`` 注册。无连接、无共享
状态、无动态工具表的工具照此写，不必手写 Provider 类。

注册方式：由组合根构造注册表后，经 ``bind_memory_tools``（见
``src/modules/memory/bootstrap.py``）把本工具注入组合根传入的注册表。

时间字段：timestamp_ms 在 Amaidesu 内部使用毫秒，本工具不引入秒/毫秒转换
（仅在切换 AMemorixProvider 时由 Provider 内部负责）。
"""

from __future__ import annotations

from typing import Any, List, Optional

from src.modules.memory.provider import MemoryProvider
from src.modules.tools.models import (
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import (
    ToolProvider,
    as_tool_impl,
    make_provider_from_specs,
)

# 提供者短名（注册名前缀 = memory_query_memory；与 provider.name 同值同源）
PROVIDER_NAME = "memory"

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
    provider=PROVIDER_NAME,
)


async def _run_query_memory(invocation: ToolInvocation, memory: Optional[MemoryProvider]) -> str:
    """query_memory 的执行体（普通 async 函数；异常交由 as_tool_impl 包装）。"""
    if memory is None:
        raise ValueError("query_memory 工具未绑定 MemoryProvider")
    args = invocation.arguments or {}
    query = str(args.get("query", "")).strip()
    try:
        top_k = int(args.get("top_k", 5))
    except (TypeError, ValueError):
        top_k = 5
    top_k = max(1, min(top_k, 20))

    if not query:
        return "（空查询）"

    hits = await memory.recall(query, top_k=top_k)
    return _format_hits(hits)


def build_query_memory_tool(memory: Optional[MemoryProvider] = None) -> ToolProvider:
    """构造 query_memory 工具的 Provider（简单工具正典路径样板）。

    ``as_tool_impl`` 把 ``_run_query_memory`` 的返回值/异常/计时归一为
    ``ToolExecutionResult``（结果回显派生全名 memory_query_memory，保持
    溯源一致）；``make_provider_from_specs`` 组装成固定 Provider
    （name=PROVIDER_NAME，category=memory）。
    """
    return make_provider_from_specs(
        PROVIDER_NAME,
        [
            (
                QUERY_MEMORY_SPEC,
                as_tool_impl(
                    QUERY_MEMORY_SPEC.full_name,
                    lambda inv: _run_query_memory(inv, memory),
                ),
            )
        ],
        category="memory",
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


__all__ = [
    "PROVIDER_NAME",
    "QUERY_MEMORY_SPEC",
    "build_query_memory_tool",
]
