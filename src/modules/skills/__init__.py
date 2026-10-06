"""Skill 模块：Agent 的玩法/操作经验文档

能力契约回答"某个能力怎么调"，知识库回答"世界里的事实是什么"，
Skill 回答"一类目标该怎么做成"——把能力与知识编排成打法。

技能文档内聚在消费方包的 ``skills/`` 目录（``src/**/skills/**/*.md``），
frontmatter 声明名字、一句话用途、受众 Agent、分类与环境前提。
消费方把目录注入系统提示词，正文经自有读取工具按需取用。

使用示例：
    ```python
    from src.modules.skills import get_skill_library, render_catalog

    library = get_skill_library()
    entries = library.catalog("minecraft", {"mods": {"create"}})
    prompt_section = render_catalog(entries)
    body = library.read("create_power_network", "minecraft", {"mods": {"create"}})
    ```
"""

from src.modules.skills.library import (
    SKILLS_DIR_NAME,
    SkillLibrary,
    get_skill_library,
    render_catalog,
    reset_skill_library,
)
from src.modules.skills.models import Skill, SkillEntry, SkillEnvironment, SkillMetadata
from src.modules.skills.tool import SKILL_TOOL_NAME, build_skill_spec

__all__ = [
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
