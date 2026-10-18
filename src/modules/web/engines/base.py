"""搜索引擎层公共件：结果模型、能力协议、HTTP 助手

引擎是 web_search / web_fetch_url 工具背后的可插拔实现。search 侧
（搜索结果从哪来）与 fetch 侧（网页正文怎么读）是两个正交能力，
各自独立登记与选择；每个引擎类只实现自己具备的能力，并提
供 ``from_config`` 类方法声明自己消费哪些配置字段。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Protocol

import aiohttp
from pydantic import BaseModel

from src.modules.logging import get_logger

logger = get_logger("WebEngines")


class SearchHit(BaseModel):
    """单条搜索结果

    title/url/snippet 是各搜索引擎都有能力给出的公共面；
    summary/date 是部分引擎（如 DeepSeek）的增值字段，格式化时按存在与否输出。
    """

    title: str
    url: str
    snippet: str = ""
    summary: str = ""
    date: str = ""


class EngineConfig(Protocol):
    """引擎可消费的配置切面（provider 的 ConfigSchema 天然满足）

    引擎经此协议按名取字段，不 import provider 的具体 Schema 类，
    保持引擎层不反向依赖工具层。
    """

    base_url: str
    user_agent: str
    timeout_ms: int
    api_key: str


class SearchEngine(Protocol):
    """搜索侧能力协议：关键词 → 结果列表"""

    async def search(self, query: str, max_results: int) -> list[SearchHit]: ...


class FetchEngine(Protocol):
    """抓页侧能力协议：网址 → 正文文本"""

    async def fetch(self, url: str) -> str: ...


class EngineHttpError(Exception):
    """引擎 HTTP 请求失败（携带状态码与响应体摘要）

    404 的语义（"无结果"还是"网址不可用"）由各引擎按端点自行判定，
    本异常只负责原样携带事实。
    """

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body[:500]
        super().__init__(f"HTTP {status}: {self.body}")


async def http_request(
    method: str,
    url: str,
    *,
    headers: Dict[str, str],
    timeout_ms: int,
    params: Optional[Dict[str, str]] = None,
    json_body: Optional[Dict[str, Any]] = None,
) -> str:
    """发起一次 HTTP 请求并返回响应文本；非 2xx 抛 EngineHttpError。

    各引擎统一经由本函数（以模块属性方式调用）发起请求，测试通过
    替换本模块的 ``http_request`` 属性即可拦截全部引擎的网络层。
    """
    async with aiohttp.ClientSession(
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout_ms / 1000),
    ) as session:
        async with session.request(method, url, params=params, json=json_body) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise EngineHttpError(resp.status, text)
            return text
