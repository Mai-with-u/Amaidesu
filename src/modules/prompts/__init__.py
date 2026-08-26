"""Prompt 管理模块

提供统一的 Prompt 模板管理功能。

模板键为声明式键：优先取模板 frontmatter 的 ``name`` 字段，
未声明时兜底为相对扫描根的路径。组件可将提示词内聚在自身包的
``prompts/`` 目录下，由管理器按 ``src/**/prompts/`` 约定自动发现。

使用示例：
    ```python
    from src.modules.prompts import get_prompt_manager

    # 获取全局单例
    prompt_mgr = get_prompt_manager()

    # 渲染模板（键来自 frontmatter 的 name）
    result = prompt_mgr.render("amaidesu_replyer", text="你好")

    # 安全模式渲染
    result = prompt_mgr.render_safe("amaidesu_replyer", text="你好")
    ```
"""

from src.modules.prompts.manager import (
    PromptManager,
    PromptTemplate,
    TemplateMetadata,
    get_prompt_manager,
    reset_prompt_manager,
)

__all__ = [
    "PromptManager",
    "PromptTemplate",
    "TemplateMetadata",
    "get_prompt_manager",
    "reset_prompt_manager",
]
