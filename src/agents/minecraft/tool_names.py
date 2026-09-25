"""按 MaiCraft 实际公布的工具名绑定，兼容已经发布过的旧前缀，不改写用户自定义绑定。"""

from collections.abc import Iterable

from src.modules.tools.models import ToolSpec


def find_mod_tool(specs: Iterable[ToolSpec], name: str) -> ToolSpec | None:
    """优先使用明确配置的原名，只有已知协议别名才回退到同一能力的另一种拼写。"""
    tools = list(specs)
    names = (name,)
    for canonical in ("perceive", "plan", "execute", "task"):
        legacy = f"maicraft_{canonical}"
        if name in (canonical, legacy):
            names = (name, legacy if name == canonical else canonical)
            break
    return next((spec for candidate in names for spec in tools if spec.name == candidate), None)
