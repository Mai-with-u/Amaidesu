"""记忆域 LLM 工具（query_memory + query_viewer_profile）

- ``query_memory(query, top_k)``：按关键词召回观众事实（``viewer_facts``）——
  符合"记忆"定义的事实查询面
- ``query_viewer_profile(platform, user_id, nickname)``：查观众画像
  （``viewer_profiles``）；昵称优先经 viewers 表反查 user_id（画像表不存
  昵称——昵称随改名漂移，权威在统计表）

**简单工具正典路径**：``ToolSpec`` + 普通 async 函数（经 ``as_tool_impl``
包装返回值/异常/计时）+ ``make_provider_from_specs`` 组装 Provider，装配处
（组合根经 ``bind_memory_tools``）一行 ``register_provider`` 注册。

时间字段：内部全毫秒零转换。
"""

from __future__ import annotations

from typing import List, Optional

from src.modules.memory.simple_memory import SimpleMemory
from src.modules.tools.models import (
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import (
    ToolProvider,
    as_tool_impl,
    make_provider_from_specs,
)

# 提供者短名（注册名前缀 = memory_query_memory / memory_query_viewer_profile）
PROVIDER_NAME = "memory"

# 查画像时的默认平台（本项目生产平台；工具参数可显式覆盖）
_DEFAULT_PLATFORM = "bilibili"

QUERY_MEMORY_SPEC = ToolSpec(
    name="query_memory",
    description=(
        "查询观众事实记忆。根据 query 关键词召回关于观众的事实条目（如喜好、观点、说过的话），每条含观众标识与内容。"
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

QUERY_VIEWER_PROFILE_SPEC = ToolSpec(
    name="query_viewer_profile",
    description=(
        "查询某位观众的画像（长期认识：偏好/性格/身份/待回应的历史）。"
        "提供 nickname（昵称）或 user_id 之一；都不知道时先用 query_memory 按关键词查事实。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "nickname": {"type": "string", "description": "观众昵称（经统计表反查用户 ID）"},
            "user_id": {"type": "string", "description": "平台用户 ID"},
            "platform": {"type": "string", "description": "平台标识，默认 bilibili"},
        },
        "required": [],
    },
    kind="sync",
    result_event="",
    provider=PROVIDER_NAME,
)


async def _run_query_memory(invocation: ToolInvocation, memory: Optional[SimpleMemory]) -> str:
    """query_memory 执行体：关键词召回观众事实。"""
    if memory is None:
        raise ValueError("query_memory 工具未绑定记忆服务")
    args = invocation.arguments or {}
    query = str(args.get("query", "")).strip()
    try:
        top_k = int(args.get("top_k", 5))
    except (TypeError, ValueError):
        top_k = 5
    top_k = max(1, min(top_k, 20))

    if not query:
        return "（空查询）"

    facts = await memory.search_viewer_facts(query=query, top_k=top_k)
    return _format_facts(facts)


async def _run_query_viewer_profile(
    invocation: ToolInvocation,
    memory: Optional[SimpleMemory],
    viewer_repo: Optional[object],
) -> str:
    """query_viewer_profile 执行体：昵称/ID 定位画像。"""
    if memory is None:
        raise ValueError("query_viewer_profile 工具未绑定记忆服务")
    args = invocation.arguments or {}
    nickname = str(args.get("nickname", "")).strip()
    user_id = str(args.get("user_id", "")).strip()
    platform = str(args.get("platform", "")).strip() or _DEFAULT_PLATFORM

    if not nickname and not user_id:
        return "（请提供 nickname 或 user_id）"

    if not user_id and nickname:
        resolved = await _resolve_user_id_by_name(viewer_repo, platform, nickname)
        if resolved is None:
            return f"（未找到昵称为「{nickname}」的观众统计行，无法定位画像）"
        user_id = resolved

    profile_text = await memory.get_viewer_profile(platform=platform, user_id=user_id)
    if not profile_text:
        return f"（观众 {platform}/{user_id} 暂无画像）"
    return f"观众 {platform}/{user_id} 的画像：\n{profile_text}"


async def _resolve_user_id_by_name(viewer_repo: Optional[object], platform: str, nickname: str) -> Optional[str]:
    """昵称 → user_id 反查（经注入的 ViewerRepo；未注入或未命中返回 None）。"""
    if viewer_repo is None:
        return None
    return await viewer_repo.get_viewer_id_by_name(platform=platform, user_name=nickname)


def build_memory_tools(memory: Optional[SimpleMemory] = None, viewer_repo: Optional[object] = None) -> ToolProvider:
    """构造记忆域双工具的 Provider（简单工具正典路径）。

    ``viewer_repo`` 为昵称反查的依赖注入点（``ViewerRepo``，鸭子类型），
    经构造器闭包显式传递给工具执行体。
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
            ),
            (
                QUERY_VIEWER_PROFILE_SPEC,
                as_tool_impl(
                    QUERY_VIEWER_PROFILE_SPEC.full_name,
                    lambda inv: _run_query_viewer_profile(inv, memory, viewer_repo),
                ),
            ),
        ],
        category="memory",
    )


def _format_facts(facts: List[object]) -> str:
    """完整返回召回事实及其时间，避免后续判断漏掉尾部条件。"""
    if not facts:
        return "（无匹配事实）"
    lines: List[str] = []
    for idx, fact in enumerate(facts, start=1):
        text = str(getattr(fact, "fact_text", "") or "")
        user = f"{getattr(fact, 'platform', '')}/{getattr(fact, 'user_id', '')}"
        created = int(getattr(fact, "created_at_ms", 0) or 0)
        lines.append(f"[{idx}] (t={created}ms, {user}) {text}")
    return "\n".join(lines)


__all__ = [
    "PROVIDER_NAME",
    "QUERY_MEMORY_SPEC",
    "QUERY_VIEWER_PROFILE_SPEC",
    "build_memory_tools",
]
