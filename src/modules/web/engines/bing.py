"""Bing 网页版搜索引擎（免 API key：抓结果页 HTML 解析）

默认 search 引擎。零凭据零成本，代价是依赖结果页 HTML 结构——
页面改版或命中验证码时解析为空列表，由调用方呈现"无结果"而非报错。
"""

from __future__ import annotations

from typing import Dict, List

from bs4 import BeautifulSoup

from src.modules.web.engines import base
from src.modules.web.engines.base import EngineConfig, SearchHit


class BingSearchEngine:
    """解析 Bing 结果页的 search 侧实现（本类不提供 fetch 能力）"""

    def __init__(self, base_url: str, user_agent: str, timeout_ms: int) -> None:
        self._base_url = base_url
        self._user_agent = user_agent
        self._timeout_ms = timeout_ms

    @classmethod
    def from_config(cls, config: EngineConfig) -> "BingSearchEngine":
        return cls(base_url=config.base_url, user_agent=config.user_agent, timeout_ms=config.timeout_ms)

    async def search(self, query: str, max_results: int) -> List[SearchHit]:
        html = await base.http_request(
            "GET",
            self._base_url,
            headers=self._headers(),
            params={"q": query},
            timeout_ms=self._timeout_ms,
        )
        return parse_search_results(html, max_results)

    def _headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self._user_agent,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }


def parse_search_results(html: str, limit: int) -> List[SearchHit]:
    """从搜索引擎结果页 HTML 解析结果条目。

    选择器面向 Bing 网页版结果结构（``li.b_algo`` 为主条目）。
    """
    soup = BeautifulSoup(html, "html.parser")
    hits: List[SearchHit] = []
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
        hits.append(SearchHit(title=title, url=url, snippet=snippet))
        if len(hits) >= limit:
            break
    return hits


__all__ = ["BingSearchEngine", "parse_search_results"]
