"""web_search / web_fetch_url 联网搜索工具

web 分类的被动工具：LLM 需要外部信息时调用，拿到文本结果即结束。
无持续循环、无长连接——每次调用是独立 HTTP 请求，探活沿用基类默认
语义（"无可检查之物，让流量决定"：失败熔断、冷却期满即恢复）。

工具层只定义"能力"，具体实现是可插拔的引擎（见 ``engines/`` 子包），
由配置独立选择、互不捆绑：

- ``web_search(query, max_results)``：关键词搜索，``search_engine``
  决定结果来源（bing 解析结果页 HTML / deepseek 搜索 API）
- ``web_fetch_url(url)``：抓取指定网址正文，``fetch_engine`` 决定
  读取方式（local 本机抓取 / deepseek 抓取 API）

TOML 段位：``[tools.web.search].config``；经 ``tools.bootstrap`` 按
分类开关装配，ConfigSchema 登记于 ``config/registry.TOOL_PROVIDER_SCHEMAS``。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Literal, Optional
from urllib.parse import urlparse

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.web.engines import SearchHit, build_fetch_engine, build_search_engine

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


class WebSearchProvider(BaseToolProvider):
    """联网搜索 ToolProvider（web 分类；无状态，无连接生命周期）

    实现契约：``invoke`` 永不抛异常，一切失败（网络/解析/参数）都转为
    ``ToolExecutionResult(success=False)``，由注册表熔断器按连续失败计数。
    两侧引擎在 ``__init__`` 按配置各构造一份；引擎内部的网络与解析失败
    以异常上抛，由 ``invoke`` 统一转失败结果。
    """

    PROVIDER_NAME = "web"
    category = "web"

    class ConfigSchema(BaseConfig):
        """web 搜索配置

        TOML 段位：[tools.web.search].config
        """

        search_engine: Literal["bing", "deepseek"] = Field(
            default="bing",
            title="搜索引擎",
            description="搜索结果来源：bing（解析网页版结果页，免费）或 deepseek（DeepSeek 搜索 API，结果质量更高，按成功请求计费）",
        )
        fetch_engine: Literal["local", "deepseek"] = Field(
            default="local",
            title="网页读取方式",
            description="读取网页正文的方式：local（本机直接抓取，免费）或 deepseek（DeepSeek 抓取 API，按成功请求计费）",
        )
        api_key: str = Field(
            default="",
            title="DeepSeek API Key",
            description=(
                "DeepSeek API 密钥；任一引擎选择 deepseek 时必填（与 DeepSeek 模型 API 为同一密钥），成功请求约 5 元/千次"
            ),
        )
        base_url: str = Field(
            default="https://www.bing.com/search",
            title="搜索入口",
            description="bing 引擎的结果页入口（默认 Bing 网页版，免 API key）",
        )
        user_agent: str = Field(
            default=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            title="浏览器标识",
            description="HTTP 请求 User-Agent（搜索引擎与网站对无 UA 请求常拒答）",
            json_schema_extra={"x-ui-advanced": True},
        )
        timeout_ms: int = Field(
            default=10000,
            ge=1000,
            le=60000,
            title="请求超时",
            description="单次请求的等待上限（毫秒）",
            json_schema_extra={"x-ui-advanced": True},
        )
        default_max_results: int = Field(
            default=5,
            ge=1,
            le=10,
            title="默认结果条数",
            description="搜索结果默认返回条数（调用方可临时覆盖）",
        )

    def __init__(self, config: Optional["WebSearchProvider.ConfigSchema"] = None) -> None:
        self._config = config or self.ConfigSchema()
        self.name = self.PROVIDER_NAME
        self._search_engine = build_search_engine(self._config.search_engine, self._config)
        self._fetch_engine = build_fetch_engine(self._config.fetch_engine, self._config)

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
        """关键词搜索：所选 search 引擎 → 结果格式化为文本列表。"""
        query = str(args.get("query", "")).strip()
        if not query:
            return "（空查询）"
        try:
            max_results = int(args.get("max_results", self._config.default_max_results))
        except (TypeError, ValueError):
            max_results = self._config.default_max_results
        max_results = max(1, min(max_results, 10))

        hits = await self._search_engine.search(query, max_results)
        if not hits:
            return f"（未搜索到与「{query}」相关的结果）"
        return format_hits(hits)

    async def _run_fetch(self, args: Dict[str, Any]) -> str:
        """抓取网址正文：scheme 校验 → 所选 fetch 引擎提取正文。"""
        url = str(args.get("url", "")).strip()
        scheme = urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"仅支持 http/https 网址，得到: {url!r}")

        text = await self._fetch_engine.fetch(url)
        if not text:
            return "（页面无可提取正文，可能需要浏览器渲染）"
        return text


# ----------------------------------------------------------------------
# 结果格式化（引擎无关，独立可测）
# ----------------------------------------------------------------------


def format_hits(hits: List[SearchHit]) -> str:
    """结果列表 → 给模型阅读的文本；summary/date 等增值字段有则附加。"""
    lines: List[str] = []
    for idx, hit in enumerate(hits, start=1):
        lines.append(f"[{idx}] {hit.title}")
        lines.append(f"    {hit.url}")
        if hit.snippet:
            lines.append(f"    {hit.snippet}")
        if hit.summary:
            lines.append(f"    摘要：{hit.summary}")
        if hit.date:
            lines.append(f"    日期：{hit.date}")
    return "\n".join(lines)


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
    subtitle_service: Optional[Any] = None,
) -> WebSearchProvider:
    """构造并注册到 registry。返回 Provider 实例供调用方管理生命周期。

    ``event_bus`` / ``lipsync_analyzer`` / ``subtitle_service`` 为 bootstrap 统一签名的占位参数，
    本 provider 无事件与虚拟形象依赖，忽略。
    """
    provider = create_web_search_provider(config=config)
    registry.register_provider(provider)
    return provider


__all__ = [
    "PROVIDER_NAME",
    "SEARCH_SPEC",
    "FETCH_URL_SPEC",
    "WebSearchProvider",
    "format_hits",
    "create_web_search_provider",
    "register_web_search_tools",
]
