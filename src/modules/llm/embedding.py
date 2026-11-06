"""OpenAI 兼容的嵌入客户端：主播记忆的语义检索把文本变成向量用。

属于基础设施，和语音合成同类：由装配代码按 ``model.toml`` 的 ``[embedding]`` 段建出来，
构造器注入给用到它的组件；不是工具，不进工具表，大模型也调不到它。

配置了才开：没填模型名时 :func:`build_embedding_client` 不建客户端，只给一句"语义检索没开"
的说明，调用方据此退回关键词与规则两路并写进日志和 WebUI。客户端自己不重试（SDK 的重试
也关掉），失败一律抛 :class:`EmbeddingError` 带上原因，由索引任务决定退避多久再试——
嵌入服务卡住时不能拖住任何一条直播通路。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

import httpx
from openai import APIError, AsyncOpenAI

from src.modules.config.model_schemas import EmbeddingConfig, LLMProviderConfig, ModelRootConfig
from src.modules.llm.clients.openai.compat import build_openai_compatible_client_config
from src.modules.logging import get_logger

logger = get_logger("EmbeddingClient")


class EmbeddingError(Exception):
    """一次嵌入请求失败：连不上、超时、服务端报错、或返回的向量条数与维度对不上。"""


class EmbeddingClient:
    """按批把文本送去 ``/embeddings``，按原顺序返回向量。

    ``model_key`` 是写进向量表 ``model`` 列的标识（模型名，截短维度时带上维度）：换模型或
    换维度后标识变了，索引任务据此整表重算，新旧向量不混用。
    """

    def __init__(self, *, sdk: AsyncOpenAI, config: EmbeddingConfig) -> None:
        self._sdk = sdk
        self._model = config.model_identifier
        self._dimensions = config.dimensions
        self._batch_size = config.batch_size
        self._timeout_s = config.timeout_ms / 1000

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
        vectors: List[List[float]] = []
        for start in range(0, len(texts), self._batch_size):
            vectors.extend(await self._embed_batch(list(texts[start : start + self._batch_size])))
        return vectors

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


def build_embedding_client(config: ModelRootConfig) -> EmbeddingSetup:
    """按 ``[embedding]`` 段建嵌入客户端；没配置就返回不带客户端的说明。

    provider 引用的有效性由配置加载期校验保证；连接信息（地址、密钥、鉴权方式、默认 header）
    与大模型客户端走同一套转换。
    """
    embedding = config.embedding
    if not embedding.enabled:
        return EmbeddingSetup(client=None, note="语义检索没开：model.toml 的 [embedding] 关着，只走关键词与规则")
    if not embedding.model_identifier:
        return EmbeddingSetup(
            client=None, note="语义检索没开：model.toml 的 [embedding] 没填嵌入模型，只走关键词与规则"
        )
    provider = _find_provider(config.llm_providers, embedding.provider)
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


def _find_provider(providers: Sequence[LLMProviderConfig], name: str) -> LLMProviderConfig:
    """按名字找 provider；配置加载期已校验过引用，这里找不到说明调用方绕过了校验。"""
    for provider in providers:
        if provider.name == name:
            return provider
    raise ValueError(f"[embedding].provider={name!r} 不在 llm_providers 里")


__all__ = ["EmbeddingClient", "EmbeddingError", "EmbeddingSetup", "build_embedding_client"]
