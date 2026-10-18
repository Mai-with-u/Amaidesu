"""本机直连抓取网页正文（免 API key 的默认 fetch 引擎）

免费、无配额，代价是受本机网络环境与目标站反爬策略限制——
JS 渲染页与拒绝无浏览器 UA 访问的站点可能拿不到正文。
"""

from __future__ import annotations

import re
from typing import Dict

from bs4 import BeautifulSoup

from src.modules.web.engines import base
from src.modules.web.engines.base import EngineConfig

# 纯文本化时整体剔除的标签
_STRIP_TAGS = ("script", "style", "noscript", "header", "footer", "nav")


class LocalFetchEngine:
    """本机 HTTP 直连 + 正文提取的 fetch 侧实现"""

    def __init__(self, user_agent: str, timeout_ms: int) -> None:
        self._user_agent = user_agent
        self._timeout_ms = timeout_ms

    @classmethod
    def from_config(cls, config: EngineConfig) -> "LocalFetchEngine":
        return cls(user_agent=config.user_agent, timeout_ms=config.timeout_ms)

    async def fetch(self, url: str) -> str:
        html = await base.http_request(
            "GET",
            url,
            headers=self._headers(),
            timeout_ms=self._timeout_ms,
        )
        return html_to_text(html)

    def _headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self._user_agent,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }


def html_to_text(html: str) -> str:
    """网页 HTML → 紧凑纯文本：剔除脚本/样式等标签，压缩连续空行。"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return re.sub(r"\n{2,}", "\n", text)


__all__ = ["LocalFetchEngine", "html_to_text"]
