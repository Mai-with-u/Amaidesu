"""
工具 → LLM 定义转换（OpenAI 风格 function calling 形状）

ToolSpec 与具体 LLM 协议之间的转换点：注册表只交付 ``ToolSpec``，LLM
视角的定义形状（含上下文计量归属标注）在此统一生成，供 Planner /
游戏 Agent 的请求规整层消费。``context_section`` 按提供者自声明的分类
区分 MCP 工具与内置工具，``result_section`` 取工具声明的结果归属；
两者只供上下文分段统计，LLM 层规整请求时收下、不发给模型。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.modules.llm.context_meter import SECTION_MCP_TOOLS, SECTION_SYSTEM_TOOLS
from src.modules.tools.models import ToolSpec
from src.modules.tools.provider import MCP_CATEGORY

if TYPE_CHECKING:
    from src.modules.tools.registry import ToolRegistry


def function_definition(spec: ToolSpec, provider_category: str) -> Dict[str, Any]:
    """单个工具的 LLM 定义（name 用派生全名）。

    Args:
        spec: 工具声明。
        provider_category: 该工具所属提供者自声明的分类（``registry.
            category_of(spec.full_name)``），用于区分上下文计量归属段。
    """
    entry: Dict[str, Any] = {
        "name": spec.full_name,
        "description": spec.description,
        "context_section": (SECTION_MCP_TOOLS if provider_category == MCP_CATEGORY else SECTION_SYSTEM_TOOLS),
    }
    if spec.parameters_schema is not None:
        entry["parameters"] = spec.parameters_schema
    if spec.result_section:
        entry["result_section"] = spec.result_section
    return entry


def to_llm_definitions(
    registry: "ToolRegistry",
    *,
    provider: Optional[str] = None,
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """返回 LLM 视角的工具定义列表（默认排除停用/熔断，与 ``list_tools`` 同口径）。"""
    return [
        function_definition(spec, registry.category_of(spec.full_name))
        for spec in registry.list_tools(provider=provider, category=category)
    ]


__all__ = ["function_definition", "to_llm_definitions"]
