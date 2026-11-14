"""OpenAI 兼容的嵌入客户端：主播记忆的语义检索把文本变成向量用。

属于基础设施，和语音合成同类：由装配代码按 ``model.toml`` 的 ``[embedding]`` 段建出来，
构造器注入给用到它的组件；不是工具，不进工具表，大模型也调不到它。

配置了才开：没填模型名、或引用的提供方没填密钥时 :func:`build_embedding_client` 不建客户端，
只给一句"语义检索没开"的说明，调用方据此退回关键词与规则两路并写进日志和 WebUI。客户端自己不重试（SDK 的重试
也关掉），失败一律抛 :class:`EmbeddingError` 带上原因——嵌入服务卡住时不能拖住任何一条直播通路。

失败后按连续失败次数翻倍退避，退避期间不发请求、直接抛错。索引补算与查询向量用的是同一个
客户端，也就共用这一套退避：服务端返回 503 时，弹幕查询不会接着一条条打过去，也不会抢索引的配额。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

import httpx
from openai import APIError, AsyncOpenAI

from src.modules.config.model_schemas import EmbeddingConfig, LLMProviderConfig
from src.modules.llm.clients.openai.compat import build_openai_compatible_client_config
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms

logger = get_logger("EmbeddingClient")

# 嵌入失败后的退避：从 30 秒起翻倍，封顶 10 分钟
BACKOFF_BASE_MS = 30_000
BACKOFF_MAX_MS = 600_000
# 送往远端嵌入接口的单段字符上限：防止超长文本撑破模型上下文上限（如 8192 token）变成永久卡住退避队列的毒丸行
MAX_EMBED_INPUT_CHARS = 6_000


class EmbeddingError(Exception):
    """一次嵌入请求失败：连不上、超时、服务端报错、或返回的向量条数与维度对不上。"""


class EmbeddingClient:
    """按批把文本送去 ``/embeddings``，按原顺序返回向量。

    ``model_key`` 是写进向量表 ``model`` 列的标识（模型名，截短维度时带上维度）：换模型或
    换维度后标识变了，索引任务据此整表重算，新旧向量不混用。
    """

    def __init__(self, *, sdk: AsyncOpenAI, config: EmbeddingConfig, clock: Callable[[], int] = now_ms) -> None:
        self._sdk = sdk
        self._model = config.model_identifier
        self._dimensions = config.dimensions
        self._batch_size = config.batch_size
        self._timeout_s = config.timeout_ms / 1000
        self._clock = clock
        self._failures = 0
        self._retry_at_ms = 0

    @property
    def failures(self) -> int:
        """连续失败了几次（成功一次就清零）。"""
        return self._failures

    @property
    def backing_off(self) -> bool:
        """正在退避：这期间 :meth:`embed` 不发请求，直接抛 :class:`EmbeddingError`。"""
        return self._failures > 0 and self._clock() < self._retry_at_ms

    @property
    def model_key(self) -> str:
        """向量表里记的模型标识。"""
        return f"{self._model}@{self._dimensions}" if self._dimensions else self._model

    async def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """把一组文本变成向量（与输入一一对应）；任何一批失败都抛 :class:`EmbeddingError`。

        空文本是调用方的错（服务端会拒），直接抛 ``ValueError``，不发请求。
        """
        if any(not text.strip() for text in texts):
            raise ValueError("嵌入的文本不能为空")
        if self.backing_off:
            wait_s = max(0, self._retry_at_ms - self._clock()) // 1000
            raise EmbeddingError(f"嵌入服务退避中（连续失败 {self._failures} 次，{wait_s} 秒后再试），这次没发请求")
        # 只截送往嵌入接口的请求字符串，不碰调用方手里的 IndexDoc.document 原文与 text_hash 指纹
        clipped = [text[:MAX_EMBED_INPUT_CHARS] if len(text) > MAX_EMBED_INPUT_CHARS else text for text in texts]
        vectors: List[List[float]] = []
        try:
            for start in range(0, len(clipped), self._batch_size):
                vectors.extend(await self._embed_batch(clipped[start : start + self._batch_size]))
        except EmbeddingError as exc:
            self._arm_backoff(exc)
            raise
        self._failures = 0
        return vectors

    def _arm_backoff(self, exc: EmbeddingError) -> None:
        """一次失败：连续失败次数加一，按次数翻倍定下次放行的时刻，记 warning（每次退避只记这一条）。"""
        self._failures += 1
        backoff_ms = min(BACKOFF_BASE_MS * 2 ** (self._failures - 1), BACKOFF_MAX_MS)
        self._retry_at_ms = self._clock() + backoff_ms
        logger.warning(
            f"嵌入服务暂不可用（连续失败 {self._failures} 次，{backoff_ms // 1000} 秒后再试；"
            f"这期间语义索引与查询向量都不发请求，关键词照常）：{exc}"
        )

    async def _embed_batch(self, batch: List[str]) -> List[List[float]]:
        """一批文本一次请求；按返回里的序号排回原顺序，再核对条数与维度。"""
        try:
            if self._dimensions:
                response = await self._sdk.embeddings.create(
                    model=self._model,
                    input=batch,
                    encoding_format="float",
                    dimensions=self._dimensions,
                    timeout=self._timeout_s,
                )
            else:
                response = await self._sdk.embeddings.create(
                    model=self._model, input=batch, encoding_format="float", timeout=self._timeout_s
                )
        except (APIError, httpx.HTTPError) as exc:
            raise EmbeddingError(f"嵌入请求失败（模型 {self._model}，{len(batch)} 段）：{exc}") from exc
        items = sorted(response.data, key=lambda item: item.index)
        if len(items) != len(batch):
            raise EmbeddingError(f"嵌入服务返回了 {len(items)} 个向量，送去的是 {len(batch)} 段")
        vectors = [list(item.embedding) for item in items]
        widths = {len(vector) for vector in vectors}
        if len(widths) != 1 or 0 in widths:
            raise EmbeddingError(f"嵌入服务返回的向量维度不一致或为空：{sorted(widths)}")
        if self._dimensions and widths != {self._dimensions}:
            raise EmbeddingError(f"配置要 {self._dimensions} 维，嵌入服务返回 {widths.pop()} 维（模型可能不支持截短）")
        return vectors


@dataclass(frozen=True)
class EmbeddingSetup:
    """装配结果：开了就有客户端；``note`` 是给日志与 WebUI 的一句话（开着用什么、没开为什么）。"""

    client: Optional[EmbeddingClient]
    note: str


def build_embedding_client(embedding: EmbeddingConfig, providers: Sequence[LLMProviderConfig]) -> EmbeddingSetup:
    """按 ``[embedding]`` 段建嵌入客户端；没配置就返回不带客户端的说明。

    嵌入只是记忆检索的加分项：关着、没填模型、引用的 provider 不在、provider 没填密钥，
    一律不建客户端、写明原因，检索退回关键词与规则，绝不因为嵌入挡住开播。
    连接信息（地址、密钥、鉴权方式、默认 header）与大模型客户端走同一套转换。
    """
    if not embedding.enabled:
        return EmbeddingSetup(client=None, note="语义检索没开：model.toml 的 [embedding] 关着，只走关键词与规则")
    if not embedding.model_identifier:
        return EmbeddingSetup(
            client=None, note="语义检索没开：model.toml 的 [embedding] 没填嵌入模型，只走关键词与规则"
        )
    provider = _find_provider(providers, embedding.provider)
    if provider is None:
        # 加载期校验会拦住落空的引用；走到这里是装配方没经过校验（如直接传原始配置），照样不挡启动
        return EmbeddingSetup(
            client=None,
            note=f"语义检索没开：嵌入未配置（llm_providers 里没有 {embedding.provider}），只走关键词与规则",
        )
    if provider.auth_type != "none" and not provider.api_key.strip():
        return EmbeddingSetup(
            client=None,
            note=(
                f"语义检索没开：嵌入未配置（model.toml 里 [[llm_providers]] 的 {provider.name} 没填 api_key），"
                "只走关键词与规则"
            ),
        )
    connection = build_openai_compatible_client_config(provider.model_dump())
    sdk = AsyncOpenAI(
        api_key=connection.api_key or "sk-dummy",
        base_url=connection.base_url,
        default_headers=connection.default_headers or None,
        default_query=connection.default_query or None,
        # 重试交给索引任务的退避：SDK 自己重试会把一次失败拖成几十秒
        max_retries=0,
    )
    client = EmbeddingClient(sdk=sdk, config=embedding)
    note = f"语义检索开着：嵌入模型 {client.model_key}（provider {provider.name}）"
    logger.info(note)
    return EmbeddingSetup(client=client, note=note)


def _find_provider(providers: Sequence[LLMProviderConfig], name: str) -> Optional[LLMProviderConfig]:
    """按名字找 provider，找不到返回 None。"""
    for provider in providers:
        if provider.name == name:
            return provider
    return None


__all__ = [
    "MAX_EMBED_INPUT_CHARS",
    "EmbeddingClient",
    "EmbeddingError",
    "EmbeddingSetup",
    "build_embedding_client",
]
