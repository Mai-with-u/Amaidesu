"""联网搜索工具测试

覆盖三面：
1. 解析纯函数（``parse_search_results`` / ``html_to_text``）——固化 HTML 片段，
   不打网络
2. ``WebSearchProvider.invoke`` 全链路——mock 掉 HTTP 层，验证分发、格式化、
   参数防御与"失败转结果不外抛"契约
3. 装配冒烟——``register_web_search_tools`` 后工具以派生全名进入 ToolRegistry
"""

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


def _invocation(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> ToolInvocation:
    return ToolInvocation(tool_name=tool_name, arguments=arguments or {})


class TestParseSearchResults:
    def test_extracts_title_url_snippet(self):
        hits = parse_search_results(_BING_HTML, limit=10)
        assert len(hits) == 2
        title, url, snippet = hits[0]
        assert title == "第一个结果"
        assert url == "https://example.com/a"
        assert snippet == "这是第一条摘要"

    def test_limit_truncates(self):
        assert len(parse_search_results(_BING_HTML, limit=1)) == 1

    def test_unparseable_page_returns_empty(self):
        """验证码/改版页 → 空列表（调用方呈现无结果，而非报错）。"""
        assert parse_search_results("<html><body>blocked</body></html>", limit=5) == []


class TestHtmlToText:
    def test_strips_script_and_compresses_blank_lines(self):
        html = (
            "<html><head><style>.x{}</style></head><body><h1>标题</h1><script>evil()</script><p>正文</p></body></html>"
        )
        text = html_to_text(html)
        assert "标题" in text and "正文" in text
        assert "evil()" not in text and ".x{}" not in text


class TestInvoke:
    def _provider(self) -> WebSearchProvider:
        return WebSearchProvider(config=WebSearchProvider.ConfigSchema())

    @staticmethod
    def _mock_http(monkeypatch, provider: WebSearchProvider, html: str) -> None:
        async def fake_get(url: str, params: Optional[Dict[str, str]] = None) -> str:
            return html

        monkeypatch.setattr(provider, "_http_get", fake_get)

    async def test_search_formats_results(self, monkeypatch):
        provider = self._provider()
        self._mock_http(monkeypatch, provider, _BING_HTML)

        result = await provider.invoke(_invocation("web_search", {"query": "测试"}))

        assert result.success is True
        assert "第一个结果" in result.content
        assert "https://example.com/a" in result.content

    async def test_fetch_url_returns_plain_text(self, monkeypatch):
        provider = self._provider()
        self._mock_http(
            monkeypatch,
            provider,
            "<html><body><h1>页标题</h1><script>bad()</script><p>页正文</p></body></html>",
        )

        result = await provider.invoke(_invocation("web_fetch_url", {"url": "https://example.com/page"}))

        assert result.success is True
        assert "页标题" in result.content and "页正文" in result.content
        assert "bad()" not in result.content

    async def test_fetch_rejects_non_http_scheme(self):
        provider = self._provider()
        result = await provider.invoke(_invocation("web_fetch_url", {"url": "ftp://example.com/x"}))
        assert result.success is False
        assert "http/https" in result.error_message

    async def test_fetch_truncates_long_content(self, monkeypatch):
        provider = WebSearchProvider(config=WebSearchProvider.ConfigSchema(max_fetch_chars=200))
        self._mock_http(monkeypatch, provider, "<html><body>" + "字" * 500 + "</body></html>")

        result = await provider.invoke(_invocation("web_fetch_url", {"url": "https://example.com/long"}))

        assert result.success is True
        assert "已截断" in result.content
        assert len(result.content) < 500

    async def test_empty_query_returns_hint_without_http(self):
        provider = self._provider()
        result = await provider.invoke(_invocation("web_search", {"query": "  "}))
        assert result.success is True
        assert "空查询" in result.content

    async def test_http_failure_becomes_failed_result(self, monkeypatch):
        """网络异常 → 失败结果（不外抛），符合 invoke 契约。"""
        provider = self._provider()

        async def boom(url: str, params: Optional[Dict[str, str]] = None) -> str:
            raise TimeoutError("连接超时")

        monkeypatch.setattr(provider, "_http_get", boom)
        result = await provider.invoke(_invocation("web_search", {"query": "测试"}))
        assert result.success is False
        assert "TimeoutError" in result.error_message

    async def test_unknown_tool_fails_without_raise(self):
        provider = self._provider()
        result = await provider.invoke(_invocation("web_nonexistent"))
        assert result.success is False


class TestAssemblySmoke:
    def test_register_adds_two_tools_with_full_names(self):
        registry = ToolRegistry()
        provider = register_web_search_tools(registry=registry, config={})

        assert provider.name == "web"
        names = {spec.full_name for spec in registry.list_tools()}
        assert {SEARCH_SPEC.full_name, FETCH_URL_SPEC.full_name} <= names
        assert SEARCH_SPEC.full_name == "web_search"
        assert FETCH_URL_SPEC.full_name == "web_fetch_url"

    def test_config_defaults_applied_from_raw_dict(self):
        provider = register_web_search_tools(registry=ToolRegistry(), config={"timeout_ms": 5000})
        assert provider._config.timeout_ms == 5000
        assert provider._config.default_max_results == 5
