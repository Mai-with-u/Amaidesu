"""联网搜索工具：解析纯函数（固化 HTML 片段不打网络）、``invoke`` 全链路（mock HTTP 层）、装配进 ToolRegistry"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from src.modules.web.search_provider import (
    FETCH_URL_SPEC,
    SEARCH_SPEC,
    WebSearchProvider,
    html_to_text,
    parse_search_results,
    register_web_search_tools,
)

# 简化版 Bing 结果页结构（li.b_algo → h2 a + .b_caption p）
_BING_HTML = """
<html><body><ol id="b_results">
<li class="b_algo"><h2><a href="https://example.com/a">第一个结果</a></h2>
  <div class="b_caption"><p>这是第一条摘要</p></div></li>
<li class="b_algo"><h2><a href="https://example.com/b">第二个结果</a></h2>
  <div class="b_caption"><p>这是第二条摘要</p></div></li>
<li class="b_algo"><p>无标题条目应被跳过</p></li>
</ol></body></html>
"""
_PAGE_HTML = (
    "<html><head><style>.x{}</style></head><body><h1>页标题</h1><script>bad()</script><p>页正文</p></body></html>"
)


def _invoke(provider: WebSearchProvider, tool_name: str, **arguments: Any):
    return provider.invoke(ToolInvocation(tool_name=tool_name, arguments=arguments))


@pytest.fixture
def provider() -> WebSearchProvider:
    return WebSearchProvider(config=WebSearchProvider.ConfigSchema())


def _serve(monkeypatch: pytest.MonkeyPatch, provider: WebSearchProvider, html: str) -> None:
    async def fake_get(url: str, params: Optional[Dict[str, str]] = None) -> str:
        return html

    monkeypatch.setattr(provider, "_http_get", fake_get)


def test_parse_search_results_extracts_hits_honours_limit_and_tolerates_unparseable_pages() -> None:
    hits = parse_search_results(_BING_HTML, limit=10)
    assert hits[0] == ("第一个结果", "https://example.com/a", "这是第一条摘要") and len(hits) == 2  # 无标题条目跳过
    assert len(parse_search_results(_BING_HTML, limit=1)) == 1
    assert parse_search_results("<html><body>blocked</body></html>", limit=5) == []  # 验证码/改版页：呈现无结果而非报错


def test_html_to_text_drops_scripts_and_styles() -> None:
    text = html_to_text(_PAGE_HTML)
    assert "页标题" in text and "页正文" in text and "bad()" not in text and ".x{}" not in text


async def test_search_formats_results_and_empty_query_skips_http(monkeypatch, provider) -> None:
    _serve(monkeypatch, provider, _BING_HTML)
    result = await _invoke(provider, "web_search", query="测试")
    assert result.success is True and "第一个结果" in result.content and "https://example.com/a" in result.content

    async def must_not_fetch(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("空查询不应发起 HTTP")

    monkeypatch.setattr(provider, "_http_get", must_not_fetch)
    blank = await _invoke(provider, "web_search", query="  ")
    assert blank.success is True and "空查询" in blank.content


async def test_fetch_url_returns_plain_text_and_keeps_long_pages_whole(monkeypatch, provider) -> None:
    _serve(monkeypatch, provider, _PAGE_HTML)
    result = await _invoke(provider, "web_fetch_url", url="https://example.com/page")
    assert result.success is True and "页正文" in result.content and "bad()" not in result.content

    _serve(monkeypatch, provider, "<html><body>" + "字" * 12000 + "</body></html>")
    long_page = await _invoke(provider, "web_fetch_url", url="https://example.com/long")
    assert long_page.content == "字" * 12000  # 尾部信息完整返回，供模型继续阅读


async def test_bad_calls_become_failed_results_instead_of_raising(monkeypatch, provider) -> None:
    ftp = await _invoke(provider, "web_fetch_url", url="ftp://example.com/x")
    assert ftp.success is False and "http/https" in ftp.error_message
    assert (await _invoke(provider, "web_nonexistent")).success is False

    async def boom(url: str, params: Optional[Dict[str, str]] = None) -> str:
        raise TimeoutError("连接超时")

    monkeypatch.setattr(provider, "_http_get", boom)
    failed = await _invoke(provider, "web_search", query="测试")
    assert failed.success is False and "TimeoutError" in failed.error_message  # 网络异常转失败结果，符合 invoke 契约


def test_register_adds_both_tools_under_derived_full_names_and_applies_config_defaults() -> None:
    registry = ToolRegistry()
    provider = register_web_search_tools(registry=registry, config={"timeout_ms": 5000})

    assert provider.name == "web"
    assert {SEARCH_SPEC.full_name, FETCH_URL_SPEC.full_name} <= {spec.full_name for spec in registry.list_tools()}
    assert (SEARCH_SPEC.full_name, FETCH_URL_SPEC.full_name) == ("web_search", "web_fetch_url")
    assert (provider._config.timeout_ms, provider._config.default_max_results) == (5000, 5)
