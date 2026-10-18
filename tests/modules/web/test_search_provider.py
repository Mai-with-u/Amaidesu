"""联网搜索工具：解析纯函数（固化 HTML/payload 不打网络）、``invoke`` 全链路（mock HTTP 层）、
引擎选择与分发、装配进 ToolRegistry"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pytest
from pydantic import ValidationError

from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from src.modules.web.engines.base import EngineHttpError, SearchHit
from src.modules.web.engines.bing import parse_search_results
from src.modules.web.engines.deepseek import DeepSeekApiEngine, parse_deepseek_results
from src.modules.web.engines.local_fetch import LocalFetchEngine, html_to_text
from src.modules.web.search_provider import (
    FETCH_URL_SPEC,
    SEARCH_SPEC,
    WebSearchProvider,
    format_hits,
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

# DeepSeek search 端点响应样例（含缺 title 的不完整条目）
_DS_PAYLOAD = json.dumps(
    {
        "results": [
            {
                "title": "深度结果一",
                "url": "https://ds.example/a",
                "snippet": "短摘要",
                "summary": "长摘要内容",
                "date": "2026-10-01T00:00:00Z",
            },
            {"title": "深度结果二", "url": "https://ds.example/b", "snippet": "第二条", "summary": "", "date": ""},
            {"url": "https://ds.example/c"},
        ]
    }
)

# 引擎统一经 base.http_request（模块属性方式）发起请求，patch 此处即拦截全部引擎网络层
_HTTP_PATCH_TARGET = "src.modules.web.engines.base.http_request"


def _invoke(provider: WebSearchProvider, tool_name: str, **arguments: Any):
    return provider.invoke(ToolInvocation(tool_name=tool_name, arguments=arguments))


def _serve(monkeypatch: pytest.MonkeyPatch, html: str) -> None:
    async def fake_request(
        method: str,
        url: str,
        *,
        headers: Dict[str, str],
        timeout_ms: int,
        params: Optional[Dict[str, str]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> str:
        return html

    monkeypatch.setattr(_HTTP_PATCH_TARGET, fake_request)


# ----------------------------------------------------------------------
# 解析纯函数
# ----------------------------------------------------------------------


def test_parse_search_results_extracts_hits_honours_limit_and_tolerates_unparseable_pages() -> None:
    hits = parse_search_results(_BING_HTML, limit=10)
    assert (hits[0].title, hits[0].url, hits[0].snippet) == (
        "第一个结果",
        "https://example.com/a",
        "这是第一条摘要",
    )
    assert len(hits) == 2  # 无标题条目跳过
    assert len(parse_search_results(_BING_HTML, limit=1)) == 1
    assert parse_search_results("<html><body>blocked</body></html>", limit=5) == []  # 验证码/改版页：呈现无结果而非报错


def test_html_to_text_drops_scripts_and_styles() -> None:
    text = html_to_text(_PAGE_HTML)
    assert "页标题" in text and "页正文" in text and "bad()" not in text and ".x{}" not in text


def test_parse_deepseek_results_maps_fields_and_skips_incomplete_entries() -> None:
    hits = parse_deepseek_results(json.loads(_DS_PAYLOAD))
    assert [hit.title for hit in hits] == ["深度结果一", "深度结果二"]  # 缺 title/url 的条目跳过
    assert hits[0].summary == "长摘要内容" and hits[0].date.startswith("2026-10-01")
    assert hits[1].summary == "" and hits[1].date == ""


def test_format_hits_appends_enrichment_fields_only_when_present() -> None:
    text = format_hits(
        [
            SearchHit(title="结果甲", url="https://a", snippet="短", summary="长摘要", date="2026-10-01"),
            SearchHit(title="结果乙", url="https://b"),
        ]
    )
    assert "[1] 结果甲" in text and "https://a" in text and "短" in text
    assert "摘要：长摘要" in text and "日期：2026-10-01" in text  # 增值字段有则附加
    assert "[2] 结果乙" in text and "摘要：" not in text.split("[2]")[1]  # 无增值字段不产生空行


# ----------------------------------------------------------------------
# 引擎选择与配置（默认值 = 改造前行为）
# ----------------------------------------------------------------------


def test_engine_selection_follows_config_and_defaults_preserve_current_behavior() -> None:
    config = WebSearchProvider.ConfigSchema(search_engine="deepseek", fetch_engine="local", api_key="sk-test")
    provider = WebSearchProvider(config=config)
    assert isinstance(provider._search_engine, DeepSeekApiEngine)
    assert isinstance(provider._fetch_engine, LocalFetchEngine)

    defaults = WebSearchProvider.ConfigSchema()
    assert (defaults.search_engine, defaults.fetch_engine, defaults.api_key) == ("bing", "local", "")

    with pytest.raises(ValidationError):
        WebSearchProvider.ConfigSchema(search_engine="yahoo")  # 未知引擎在配置校验期硬错


# ----------------------------------------------------------------------
# provider 全链路（默认 bing/local 引擎）
# ----------------------------------------------------------------------


async def test_search_formats_results_and_empty_query_skips_http(monkeypatch, provider) -> None:
    _serve(monkeypatch, _BING_HTML)
    result = await _invoke(provider, "web_search", query="测试")
    assert result.success is True and "第一个结果" in result.content and "https://example.com/a" in result.content

    async def must_not_request(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("空查询不应发起 HTTP")

    monkeypatch.setattr(_HTTP_PATCH_TARGET, must_not_request)
    blank = await _invoke(provider, "web_search", query="  ")
    assert blank.success is True and "空查询" in blank.content


async def test_fetch_url_returns_plain_text_and_keeps_long_pages_whole(monkeypatch, provider) -> None:
    _serve(monkeypatch, _PAGE_HTML)
    result = await _invoke(provider, "web_fetch_url", url="https://example.com/page")
    assert result.success is True and "页正文" in result.content and "bad()" not in result.content

    _serve(monkeypatch, "<html><body>" + "字" * 12000 + "</body></html>")
    long_page = await _invoke(provider, "web_fetch_url", url="https://example.com/long")
    assert long_page.content == "字" * 12000  # 尾部信息完整返回，供模型继续阅读


async def test_bad_calls_become_failed_results_instead_of_raising(monkeypatch, provider) -> None:
    ftp = await _invoke(provider, "web_fetch_url", url="ftp://example.com/x")
    assert ftp.success is False and "http/https" in ftp.error_message
    assert (await _invoke(provider, "web_nonexistent")).success is False

    async def boom(
        method: str,
        url: str,
        *,
        headers: Dict[str, str],
        timeout_ms: int,
        params: Optional[Dict[str, str]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> str:
        raise TimeoutError("连接超时")

    monkeypatch.setattr(_HTTP_PATCH_TARGET, boom)
    failed = await _invoke(provider, "web_search", query="测试")
    assert failed.success is False and "TimeoutError" in failed.error_message  # 网络异常转失败结果，符合 invoke 契约


# ----------------------------------------------------------------------
# DeepSeek 引擎行为
# ----------------------------------------------------------------------


async def test_deepseek_search_404_is_soft_empty_and_401_is_failure(monkeypatch) -> None:
    provider = WebSearchProvider(
        config=WebSearchProvider.ConfigSchema(search_engine="deepseek", api_key="sk-test")
    )

    async def not_found(
        method: str,
        url: str,
        *,
        headers: Dict[str, str],
        timeout_ms: int,
        params: Optional[Dict[str, str]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> str:
        raise EngineHttpError(404, '{"error": "no available content"}')

    monkeypatch.setattr(_HTTP_PATCH_TARGET, not_found)
    result = await _invoke(provider, "web_search", query="冷门词")
    assert result.success is True and "未搜索到" in result.content  # 无结果属正常空态，不计失败防熔断

    async def unauthorized(
        method: str,
        url: str,
        *,
        headers: Dict[str, str],
        timeout_ms: int,
        params: Optional[Dict[str, str]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> str:
        raise EngineHttpError(401, '{"error": {"message": "authentication failed"}}')

    monkeypatch.setattr(_HTTP_PATCH_TARGET, unauthorized)
    failed = await _invoke(provider, "web_search", query="测试")
    assert failed.success is False and "401" in failed.error_message


async def test_deepseek_search_truncates_query_and_clamps_count(monkeypatch) -> None:
    engine = DeepSeekApiEngine(api_key="sk-test")
    captured: Dict[str, Any] = {}

    async def capture(
        method: str,
        url: str,
        *,
        headers: Dict[str, str],
        timeout_ms: int,
        params: Optional[Dict[str, str]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> str:
        captured.update({"url": url, "json": json_body, "auth": headers.get("Authorization")})
        return json.dumps({"results": []})

    monkeypatch.setattr(_HTTP_PATCH_TARGET, capture)
    hits = await engine.search("词" * 200, max_results=999)
    assert hits == []
    assert captured["url"].endswith("/v1/tools/search")
    assert len(captured["json"]["query"]) == 128  # DeepSeek 接口 query 上限，超长截断
    assert captured["json"]["count"] == 50
    assert captured["json"]["need_content"] is False
    assert captured["auth"] == "Bearer sk-test"


async def test_deepseek_fetch_returns_markdown_content(monkeypatch) -> None:
    provider = WebSearchProvider(
        config=WebSearchProvider.ConfigSchema(search_engine="bing", fetch_engine="deepseek", api_key="sk-test")
    )
    captured: Dict[str, Any] = {}

    async def capture(
        method: str,
        url: str,
        *,
        headers: Dict[str, str],
        timeout_ms: int,
        params: Optional[Dict[str, str]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> str:
        captured.update({"url": url, "json": json_body})
        return json.dumps({"title": "页题", "content": "# 标题\n正文段落"})

    monkeypatch.setattr(_HTTP_PATCH_TARGET, capture)
    result = await _invoke(provider, "web_fetch_url", url="https://example.com/page")
    assert result.success is True
    assert "# 标题" in result.content and "正文段落" in result.content
    assert captured["url"].endswith("/v1/tools/fetch") and captured["json"] == {"url": "https://example.com/page"}


async def test_deepseek_without_key_yields_failed_result_with_guidance() -> None:
    provider = WebSearchProvider(config=WebSearchProvider.ConfigSchema(search_engine="deepseek"))
    result = await _invoke(provider, "web_search", query="测试")
    assert result.success is False and "api_key" in result.error_message  # 未配置密钥：运行期失败并给出配置出路


# ----------------------------------------------------------------------
# 装配
# ----------------------------------------------------------------------


def test_register_adds_both_tools_under_derived_full_names_and_applies_config_defaults() -> None:
    registry = ToolRegistry()
    provider = register_web_search_tools(registry=registry, config={"timeout_ms": 5000})

    assert provider.name == "web"
    assert {SEARCH_SPEC.full_name, FETCH_URL_SPEC.full_name} <= {spec.full_name for spec in registry.list_tools()}
    assert (SEARCH_SPEC.full_name, FETCH_URL_SPEC.full_name) == ("web_search", "web_fetch_url")
    assert (provider._config.timeout_ms, provider._config.default_max_results) == (5000, 5)
    assert (provider._config.search_engine, provider._config.fetch_engine) == ("bing", "local")


@pytest.fixture
def provider() -> WebSearchProvider:
    return WebSearchProvider(config=WebSearchProvider.ConfigSchema())
