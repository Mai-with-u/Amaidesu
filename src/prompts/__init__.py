"""Prompt 管理模块

提供统一的 Prompt 模板管理功能。

使用示例：
    ```python
    from src.prompts import get_prompt_manager

    # 获取全局单例
    prompt_mgr = get_prompt_manager()

    # 渲染模板
    result = prompt_mgr.render("decision/intent", user_name="Alice", message="你好")

    # 安全模式渲染
    result = prompt_mgr.render_safe("decision/intent", user_name="Alice")
    ```
"""

from src.prompts.manager import (
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
