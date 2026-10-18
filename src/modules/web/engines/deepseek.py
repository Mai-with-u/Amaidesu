"""DeepSeek 搜索/抓页 API 引擎（独立端点 /v1/tools/*，按成功请求计费）

与 DeepSeek 模型 API 同一密钥（Bearer）、同一 Base URL，但能力是
原始数据（结构化结果 / Markdown 正文）而非模型回答。两侧端点独立
限速（各 5 QPS，按账号计）；搜索无结果返回 404 且不计费。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from src.modules.web.engines import base
from src.modules.web.engines.base import EngineConfig, EngineHttpError, SearchHit

SEARCH_ENDPOINT = "/v1/tools/search"
FETCH_ENDPOINT = "/v1/tools/fetch"

# DeepSeek search 接口的 query 长度上限（Unicode 字符）
MAX_QUERY_CHARS = 128
# DeepSeek search 接口的单次结果条数上限
MAX_COUNT = 50


class DeepSeekApiEngine:
    """同时实现 search 与 fetch 两侧的引擎

    search 走服务端搜索拿结构化结果（含适合直接喂模型的长摘要
    summary 与内容日期）；fetch 走服务端抓取，Markdown 正文直出。
    未配置密钥时在调用期报错（构造期不拦，配置约定以空串表达"未配置"）。
    """

    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com", timeout_ms: int = 10000) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_ms = timeout_ms

    @classmethod
    def from_config(cls, config: EngineConfig) -> "DeepSeekApiEngine":
        return cls(api_key=config.api_key, timeout_ms=config.timeout_ms)

    async def search(self, query: str, max_results: int) -> List[SearchHit]:
        self._ensure_key()
        payload = {
            "query": query[:MAX_QUERY_CHARS],
            "count": max(1, min(max_results, MAX_COUNT)),
            "need_content": False,
        }
        try:
            text = await base.http_request(
                "POST",
                self._base_url + SEARCH_ENDPOINT,
                headers=self._headers(),
                json_body=payload,
                timeout_ms=self._timeout_ms,
            )
        except EngineHttpError as exc:
            if exc.status == 404:
                return []  # "无可用结果"是正常空态而非故障：转空列表，避免熔断器误计连续失败
            raise
        return parse_deepseek_results(json.loads(text))

    async def fetch(self, url: str) -> str:
        self._ensure_key()
        text = await base.http_request(
            "POST",
            self._base_url + FETCH_ENDPOINT,
            headers=self._headers(),
            json_body={"url": url},
            timeout_ms=self._timeout_ms,
        )
        data = json.loads(text)
        return str(data.get("content") or "")

    def _ensure_key(self) -> None:
        if not self._api_key:
            raise ValueError(
                "DeepSeek api_key 未配置：search_engine/fetch_engine 选择 deepseek 时"
                "需在 [tools.web.search].config 的 api_key 填入密钥"
            )

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }


def parse_deepseek_results(payload: Dict[str, Any]) -> List[SearchHit]:
    """解析 search 端点响应为结果列表（缺 title/url 的条目跳过）。"""
    hits: List[SearchHit] = []
    for item in payload.get("results") or []:
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not url:
            continue
        hits.append(
            SearchHit(
                title=title,
                url=url,
                snippet=str(item.get("snippet") or ""),
                summary=str(item.get("summary") or ""),
                date=str(item.get("date") or ""),
            )
        )
    return hits


__all__ = ["DeepSeekApiEngine", "parse_deepseek_results", "SEARCH_ENDPOINT", "FETCH_ENDPOINT", "MAX_QUERY_CHARS"]
