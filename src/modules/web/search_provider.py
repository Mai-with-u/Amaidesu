"""web_search / web_fetch_url 联网搜索工具

web 分类的被动工具：LLM 需要外部信息时调用，拿到文本结果即结束。
无持续循环、无长连接——每次调用是独立 HTTP 请求，探活沿用基类默认
语义（"无可检查之物，让流量决定"：失败熔断、冷却期满即恢复）。

- ``web_search(query, max_results)``：关键词搜索。解析搜索引擎结果页
  HTML（默认 Bing 网页版，免 API key），返回标题+网址+摘要文本列表；
  ``base_url`` 可指向其他同构搜索引擎入口
- ``web_fetch_url(url)``：抓取指定网址正文——去除脚本/样式标签后提取
  纯文本，压缩空白并按配置截断，用于阅读搜索结果中的具体页面

TOML 段位：``[tools.web.search].config``；经 ``tools.bootstrap`` 按
分类开关装配，ConfigSchema 登记于 ``config/registry.TOOL_PROVIDER_SCHEMAS``。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup
from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider

logger = get_logger("WebSearch")

PROVIDER_NAME = "web"

SEARCH_SPEC = ToolSpec(
    name="search",
    description=(
        "联网搜索。根据关键词返回网页搜索结果列表（每条含标题、网址、摘要），适合查询实时信息、事实核对、资料检索。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        },
        "required": ["query"],
    },
    kind="sync",
    result_event="",
    provider=PROVIDER_NAME,
)

FETCH_URL_SPEC = ToolSpec(
    name="fetch_url",
    description=(
        "读取指定网址的网页正文文本（纯文本，非原始 HTML）。用于阅读搜索结果中的具体页面，或打开对话中给出的链接。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "完整网址（以 http:// 或 https:// 开头）"},
        },
        "required": ["url"],
    },
    kind="sync",
    result_event="",
    provider=PROVIDER_NAME,
)

# 结果条目（解析中间态：title / url / snippet）
_Hit = tuple[str, str, str]

# 纯文本化时整体剔除的标签
_STRIP_TAGS = ("script", "style", "noscript", "header", "footer", "nav")


class WebSearchProvider(BaseToolProvider):
    """联网搜索 ToolProvider（web 分类；无状态，无连接生命周期）

    实现契约：``invoke`` 永不抛异常，一切失败（网络/解析/参数）都转为
    ``ToolExecutionResult(success=False)``，由注册表熔断器按连续失败计数。
    HTTP 走 aiohttp 异步请求，不阻塞事件循环；结果页解析为纯函数（见
    模块级 ``parse_search_results`` / ``html_to_text``），独立可测。
    """

    PROVIDER_NAME = "web"
    category = "web"

    class ConfigSchema(BaseConfig):
        """web 搜索配置

        TOML 段位：[tools.web.search].config
        """

        base_url: str = Field(
            default="https://cn.bing.com/search",
            description="搜索引擎结果页入口（默认 Bing 网页版，免 API key）",
        )
        user_agent: str = Field(
            default=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            description="HTTP 请求 User-Agent（搜索引擎与网站对无 UA 请求常拒答）",
        )
        timeout_ms: int = Field(
            default=10000,
            ge=1000,
            le=60000,
            description="单次 HTTP 请求超时（毫秒）",
        )
        default_max_results: int = Field(
            default=5,
            ge=1,
            le=10,
            description="搜索结果默认返回条数（调用方可用 max_results 覆盖）",
        )
        max_fetch_chars: int = Field(
            default=4000,
            ge=200,
            description="网页正文最大返回字符数（超出截断并附提示）",
        )

    def __init__(self, config: Optional["WebSearchProvider.ConfigSchema"] = None) -> None:
        self._config = config or self.ConfigSchema()
        self.name = self.PROVIDER_NAME

    def list_tools(self) -> Iterable[ToolSpec]:
        return [SEARCH_SPEC, FETCH_URL_SPEC]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        started_ms = now_ms()
        try:
            if invocation.tool_name == SEARCH_SPEC.full_name:
                content = await self._run_search(invocation.arguments or {})
            elif invocation.tool_name == FETCH_URL_SPEC.full_name:
                content = await self._run_fetch(invocation.arguments or {})
            else:
                return ToolExecutionResult(
                    tool_name=invocation.tool_name,
                    success=False,
                    error_message=f"工具 '{invocation.tool_name}' 不属于 Provider '{self.PROVIDER_NAME}'",
                )
        except Exception as exc:  # noqa: BLE001 - 工具边界兜底：失败转结果不外抛
            logger.error(
                f"工具 '{invocation.tool_name}' 执行失败: {type(exc).__name__}: {exc}",
                exc=True,
            )
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=f"{type(exc).__name__}: {exc}",
                timestamp_ms=now_ms(),
                duration_ms=now_ms() - started_ms,
            )
        finished_ms = now_ms()
        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=True,
            content=content,
            timestamp_ms=finished_ms,
            duration_ms=finished_ms - started_ms,
        )

    # ------------------------------------------------------------------
    # 工具执行体
    # ------------------------------------------------------------------

    async def _run_search(self, args: Dict[str, Any]) -> str:
        """关键词搜索：抓结果页 → 解析 → 格式化为文本列表。"""
        query = str(args.get("query", "")).strip()
        if not query:
            return "（空查询）"
        try:
            max_results = int(args.get("max_results", self._config.default_max_results))
        except (TypeError, ValueError):
            max_results = self._config.default_max_results
        max_results = max(1, min(max_results, 10))

        html = await self._http_get(self._config.base_url, params={"q": query})
        hits = parse_search_results(html, max_results)
        if not hits:
            return f"（未搜索到与「{query}」相关的结果）"
        lines: List[str] = []
        for idx, (title, url, snippet) in enumerate(hits, start=1):
            lines.append(f"[{idx}] {title}")
            lines.append(f"    {url}")
            if snippet:
                lines.append(f"    {snippet}")
        return "\n".join(lines)

    async def _run_fetch(self, args: Dict[str, Any]) -> str:
        """抓取网址正文：scheme 校验 → 拉取 → 纯文本化 → 截断。"""
        url = str(args.get("url", "")).strip()
        scheme = urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"仅支持 http/https 网址，得到: {url!r}")

        html = await self._http_get(url)
        text = html_to_text(html)
        limit = self._config.max_fetch_chars
        if len(text) > limit:
            return text[:limit] + f"\n（正文过长，已截断至前 {limit} 字符）"
        if not text:
            return "（页面无可提取正文，可能需要浏览器渲染）"
        return text

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    async def _http_get(self, url: str, params: Optional[Dict[str, str]] = None) -> str:
        """异步 GET，返回响应文本（非 2xx 抛异常，由 invoke 统一转失败结果）。"""
        async with aiohttp.ClientSession(
            headers=self._http_headers(),
            timeout=aiohttp.ClientTimeout(total=self._config.timeout_ms / 1000),
        ) as session:
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                return await resp.text()

    def _http_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self._config.user_agent,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }


# ----------------------------------------------------------------------
# 模块级解析函数（纯函数，独立可测）
# ----------------------------------------------------------------------


def parse_search_results(html: str, limit: int) -> List[_Hit]:
    """从搜索引擎结果页 HTML 解析结果条目（标题/网址/摘要）。

    选择器面向 Bing 网页版结果结构（``li.b_algo`` 为主条目）；页面改版或
    命中验证码时解析为空列表，由调用方呈现"无结果"而非报错。
    """
    soup = BeautifulSoup(html, "html.parser")
    hits: List[_Hit] = []
    for li in soup.select("li.b_algo"):
        anchor = li.select_one("h2 a")
        if anchor is None:
            continue
        title = anchor.get_text(strip=True)
        url = str(anchor.get("href") or "").strip()
        if not title or not url:
            continue
        caption = li.select_one(".b_caption p") or li.select_one("p")
        snippet = caption.get_text(strip=True) if caption is not None else ""
        hits.append((title, url, snippet))
        if len(hits) >= limit:
            break
    return hits


def html_to_text(html: str) -> str:
    """网页 HTML → 紧凑纯文本：剔除脚本/样式等标签，压缩连续空行。"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return re.sub(r"\n{2,}", "\n", text)


# ----------------------------------------------------------------------
# 装配入口（bootstrap 统一签名）
# ----------------------------------------------------------------------


def create_web_search_provider(config: Dict[str, Any]) -> WebSearchProvider:
    """构造 WebSearchProvider（config 为 [tools.web.search].config 原始 dict）"""
    schema = WebSearchProvider.ConfigSchema.model_validate(config)
    return WebSearchProvider(config=schema)


def register_web_search_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[Any] = None,
    lipsync_analyzer: Optional[Any] = None,
) -> WebSearchProvider:
    """构造并注册到 registry。返回 Provider 实例供调用方管理生命周期。

    ``event_bus`` / ``lipsync_analyzer`` 为 bootstrap 统一签名的占位参数，
    本 provider 无事件与皮套依赖，忽略。
    """
    provider = create_web_search_provider(config=config)
    registry.register_provider(provider)
    return provider


__all__ = [
    "PROVIDER_NAME",
    "SEARCH_SPEC",
    "FETCH_URL_SPEC",
    "WebSearchProvider",
    "parse_search_results",
    "html_to_text",
    "create_web_search_provider",
    "register_web_search_tools",
]
