"""按模型查询所属 provider 的平台额度（带 TTL 缓存与并发合并）。"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any, Callable, Dict, Optional, Protocol, Tuple

import aiohttp

from src.modules.logging import get_logger

from .models import ProviderQuota
from .platforms import QuotaFetchError, QuotaPlatform, resolve_platform

logger = get_logger("ProviderQuota")


class ModelProviderLookup(Protocol):
    """能按 API 模型标识反查 provider 的对象（LLMManager 满足此协议）。"""

    def get_model_provider(self, model_identifier: str) -> Optional[Tuple[str, Dict[str, Any]]]: ...


def _now_ms() -> int:
    return int(time.time() * 1000)


class ProviderQuotaService:
    """Dashboard 上下文面板 Usage 区的数据源。

    悬停上下文水位时按模型触发查询；同一 provider（同 base_url + Key + 平台设置）
    在 TTL 内复用上次结果，"不支持 / 查询失败"的 None 结果同样缓存，避免面板
    反复弹出时把平台接口打满。并发的同 provider 查询合并为一次。
    """

    def __init__(
        self,
        llm_manager: ModelProviderLookup,
        *,
        ttl_ms: int = 60_000,
        timeout_ms: int = 10_000,
        clock: Callable[[], int] = _now_ms,
    ) -> None:
        self._llm_manager = llm_manager
        self._ttl_ms = ttl_ms
        self._timeout_ms = timeout_ms
        self._clock = clock
        # 缓存键 → (过期时刻毫秒, 查询结果)
        self._cache: Dict[str, Tuple[int, Optional[ProviderQuota]]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    async def get_quota_for_model(self, model_identifier: str) -> Optional[ProviderQuota]:
        """查询模型所属 provider 的额度；未知模型、平台不支持或查询失败返回 None。"""
        resolved = self._llm_manager.get_model_provider(model_identifier)
        if resolved is None:
            return None
        provider_name, provider_config = resolved
        base_url = str(provider_config.get("base_url") or "")
        api_key = str(provider_config.get("api_key") or "").strip()
        platform = resolve_platform(base_url, str(provider_config.get("quota_platform") or "auto"))
        if platform is None or not api_key:
            return None

        # 键里只放 Key 的摘要：缓存字典常驻内存，不额外保留明文 Key
        key_digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
        cache_key = f"{provider_name}|{platform.name}|{base_url}|{key_digest}"
        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = self._cache.get(cache_key)
            if cached is not None and cached[0] > self._clock():
                return cached[1]
            result = await self._fetch(provider_name, platform, base_url, api_key)
            self._cache[cache_key] = (self._clock() + self._ttl_ms, result)
            return result

    async def _fetch(
        self,
        provider_name: str,
        platform: QuotaPlatform,
        base_url: str,
        api_key: str,
    ) -> Optional[ProviderQuota]:
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        # trust_env 让查询走系统代理，与推理请求（OpenAI SDK 默认读代理环境变量）同路
        async with aiohttp.ClientSession(
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=self._timeout_ms / 1000),
            trust_env=True,
        ) as session:

            async def get_json(url: str) -> Any:
                async with session.get(url) as resp:
                    if resp.status >= 400:
                        raise QuotaFetchError(f"{url} 返回 HTTP {resp.status}")
                    try:
                        return await resp.json(content_type=None)
                    except ValueError as exc:
                        raise QuotaFetchError(f"{url} 返回的不是 JSON") from exc

            try:
                quota = await platform.fetch(get_json, base_url)
            except (QuotaFetchError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.warning(f"provider '{provider_name}' 的 {platform.label} 额度查询失败: {exc!r}", exc=exc)
                return None

        if quota is None:
            logger.debug(f"provider '{provider_name}' 的 {platform.label} 额度接口没有可展示的条目")
            return None
        return ProviderQuota(
            provider_name=provider_name,
            platform=platform.name,
            platform_label=platform.label,
            plan_label=quota.plan_label,
            windows=quota.windows,
            fetched_at_ms=self._clock(),
        )
