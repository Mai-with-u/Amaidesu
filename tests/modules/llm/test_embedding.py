"""嵌入客户端：分批、按序号排回、核对条数与维度，失败一律变成 EmbeddingError；没配置就不建客户端。

用真实的 OpenAI SDK 发请求，传输层换成进程内的假服务端，请求体与返回体都按 ``/embeddings``
的线上格式走。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

import httpx
import pytest
from openai import AsyncOpenAI

from src.modules.config.model_schemas import EmbeddingConfig, LLMProviderConfig
from src.modules.llm.embedding import EmbeddingClient, EmbeddingError, build_embedding_client

Handler = Callable[[Dict[str, Any]], httpx.Response]


def _client(handler: Handler, *, requests: List[Dict[str, Any]], **config: Any) -> EmbeddingClient:
    """建一个连着假服务端的客户端；每次请求的 JSON 体记进 ``requests``。"""

    def _route(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        return handler(body)

    sdk = AsyncOpenAI(
        api_key="sk-test",
        base_url="https://embed.example/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_route)),
    )
    return EmbeddingClient(sdk=sdk, config=EmbeddingConfig(model_identifier="bge-m3", provider="default", **config))


def _vectors_reply(body: Dict[str, Any], *, width: int = 3, reverse: bool = True) -> httpx.Response:
    """按输入条数回向量；第 i 段的向量是 [i, i, ...]，默认倒序返回，考验按序号排回。"""
    items = [{"object": "embedding", "index": i, "embedding": [float(i)] * width} for i in range(len(body["input"]))]
    if reverse:
        items.reverse()
    return httpx.Response(200, json={"object": "list", "data": items, "model": body["model"]})


async def test_batches_and_restores_input_order() -> None:
    requests: List[Dict[str, Any]] = []
    client = _client(_vectors_reply, requests=requests, batch_size=2)

    vectors = await client.embed(["芝麻", "水渠", "甘蔗", "熔炉", "鳕鱼"])

    assert [len(body["input"]) for body in requests] == [2, 2, 1]
    assert [vector[0] for vector in vectors] == [0.0, 1.0, 0.0, 1.0, 0.0]
    assert "dimensions" not in requests[0], "没配维度时不让服务端截短"
    assert client.model_key == "bge-m3"


async def test_configured_dimensions_are_requested_and_checked() -> None:
    requests: List[Dict[str, Any]] = []
    client = _client(lambda body: _vectors_reply(body, width=4), requests=requests, dimensions=4)

    assert [len(vector) for vector in await client.embed(["芝麻"])] == [4]
    assert requests[0]["dimensions"] == 4
    assert client.model_key == "bge-m3@4", "换维度要整表重算，标识里带上维度"

    wrong = _client(lambda body: _vectors_reply(body, width=3), requests=[], dimensions=4)
    with pytest.raises(EmbeddingError, match="4 维"):
        await wrong.embed(["芝麻"])


@pytest.mark.parametrize(
    ("handler", "reason"),
    [
        (lambda body: httpx.Response(500, json={"error": {"message": "boom"}}), "嵌入请求失败"),
        (lambda body: httpx.Response(404, json={"error": {"message": "model not found"}}), "嵌入请求失败"),
        (lambda body: _vectors_reply({"input": body["input"][:-1], "model": "m"}), "返回了 1 个向量"),
        (lambda body: _vectors_reply(body, width=0), "维度不一致或为空"),
    ],
)
async def test_failures_become_embedding_errors_with_a_reason(handler: Handler, reason: str) -> None:
    client = _client(handler, requests=[])
    with pytest.raises(EmbeddingError, match=reason):
        await client.embed(["芝麻", "水渠"])


async def test_connection_failure_is_an_embedding_error() -> None:
    def _refuse(_body: Dict[str, Any]) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(EmbeddingError, match="嵌入请求失败"):
        await _client(_refuse, requests=[]).embed(["芝麻"])


async def test_empty_text_is_rejected_without_a_request() -> None:
    requests: List[Dict[str, Any]] = []
    with pytest.raises(ValueError, match="不能为空"):
        await _client(_vectors_reply, requests=requests).embed(["芝麻", "  "])
    assert requests == []


def test_not_configured_builds_no_client_and_says_why() -> None:
    providers = [LLMProviderConfig()]
    setup = build_embedding_client(EmbeddingConfig(), providers)
    assert setup.client is None and "没填嵌入模型" in setup.note

    disabled = build_embedding_client(EmbeddingConfig(enabled=False, model_identifier="bge-m3"), providers)
    assert disabled.client is None and "关着" in disabled.note


def test_configured_builds_a_client_named_in_the_note() -> None:
    providers = [LLMProviderConfig(), LLMProviderConfig(name="local", base_url="http://127.0.0.1:11434/v1")]
    embedding = EmbeddingConfig(provider="local", model_identifier="bge-m3", dimensions=512)
    setup = build_embedding_client(embedding, providers)
    assert setup.client is not None and setup.client.model_key == "bge-m3@512"
    assert "bge-m3@512" in setup.note and "local" in setup.note
