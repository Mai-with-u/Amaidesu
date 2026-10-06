"""技能正文读取工具的声明

读取工具由各消费 Agent 在自己的工具提供者里声明（全名 ``<provider>_skill``），
按 Agent 内部工具惯例注册并只对自己可见：目录按受众筛选，读取也必须以同一个
Agent 身份进行，而工具注册表调用不携带可信的调用方身份，所以不做全局共享工具。
"""

from __future__ import annotations

from src.modules.llm.context_meter import SECTION_SKILLS
from src.modules.tools.models import ToolSpec

SKILL_TOOL_NAME = "skill"


def build_skill_spec(provider: str) -> ToolSpec:
    """技能正文读取工具规格（provider 为消费 Agent 的工具提供者名）。

    读到的正文在上下文计量中记入技能段，与技能目录合并显示。
    """
    return ToolSpec(
        name=SKILL_TOOL_NAME,
        provider=provider,
        kind="sync",
        result_section=SECTION_SKILLS,
        description=(
            "按名读取技能目录中一项技能的正文。技能是把一类目标做成的打法与经验：步骤、决策点、常见坑和该查的资料。"
            "开始一类不熟悉或容易出错的工作前读取相关技能；同一任务里读过的正文仍在上下文时直接复用，不重复读取。"
            "技能是经验参考，不授予额外权限，也不替代当前现场证据与能力契约。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "description": "技能目录中的技能名"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    )


__all__ = ["SKILL_TOOL_NAME", "build_skill_spec"]
