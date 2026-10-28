"""Skill 模块：Agent 的玩法/操作经验文档

能力契约回答"某个能力怎么调"，知识库回答"世界里的事实是什么"，
Skill 回答"一类目标该怎么做成"——把能力与知识编排成可执行的做法。

玩法技能随能力提供方（MaiCraft）发布，经 MCP 的 ``skill://`` 资源下发；
宿主自己只保留直播与上报策略。两边在目录里合并，本地技能写在
``src/**/skills/**/*.md``，远端技能由消费 Agent 从服务器目录登记。

使用示例：
    ```python
    from src.modules.skills import get_skill_library, render_catalog

    library = get_skill_library()
    entries = library.catalog("minecraft", {"mods": {"create"}})
    prompt_section = render_catalog(entries)
    body = library.read("streaming_policy", "minecraft")
    ```
"""

from src.modules.skills.library import (
    SKILLS_DIR_NAME,
    SkillLibrary,
    get_skill_library,
    render_catalog,
    reset_skill_library,
)
from src.modules.skills.models import RemoteSkill, Skill, SkillEntry, SkillEnvironment, SkillMetadata
from src.modules.skills.tool import SKILL_TOOL_NAME, build_skill_spec

__all__ = [
    "RemoteSkill",
    "SKILLS_DIR_NAME",
    "SKILL_TOOL_NAME",
    "Skill",
    "SkillEntry",
    "SkillEnvironment",
    "SkillLibrary",
    "SkillMetadata",
    "build_skill_spec",
    "get_skill_library",
    "render_catalog",
    "reset_skill_library",
]
