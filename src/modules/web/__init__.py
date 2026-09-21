"""web 工具模块

提供联网信息获取工具：
- ``web_search``      - 关键词搜索（Bing 网页版解析，免 API key）
- ``web_fetch_url``   - 抓取指定网址的正文纯文本
"""

from .search_provider import (
    FETCH_URL_SPEC,
    SEARCH_SPEC,
    WebSearchProvider,
    create_web_search_provider,
    html_to_text,
    parse_search_results,
    register_web_search_tools,
)

__all__ = [
    "FETCH_URL_SPEC",
    "SEARCH_SPEC",
    "WebSearchProvider",
    "create_web_search_provider",
    "html_to_text",
    "parse_search_results",
    "register_web_search_tools",
]
