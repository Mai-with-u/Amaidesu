"""web 工具模块

提供联网信息获取工具（能力固定，实现引擎可插拔，见 ``engines/``）：
- ``web_search``      - 关键词搜索（搜索引擎由 search_engine 配置选择）
- ``web_fetch_url``   - 抓取指定网址的正文文本（读取方式由 fetch_engine 配置选择）
"""

from .search_provider import (
    FETCH_URL_SPEC,
    SEARCH_SPEC,
    WebSearchProvider,
    create_web_search_provider,
    register_web_search_tools,
)

__all__ = [
    "FETCH_URL_SPEC",
    "SEARCH_SPEC",
    "WebSearchProvider",
    "create_web_search_provider",
    "register_web_search_tools",
]
