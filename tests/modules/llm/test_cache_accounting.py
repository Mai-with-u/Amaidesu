"""缓存 token 记账：客户端归一化厂商缓存字段 → record_usage 落 llm_usage（未上报落 0）→ 分段计价判据"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import patch

import pytest

from src.modules.llm.client import BaseLLMClient
from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.clients.openai.client import OpenAIClient
from src.modules.llm.engine import LLMManager
from src.modules.llm.observation import calculate_cost, record_usage
from src.modules.llm.payload import GenerateRequest, Response, Usage
from src.modules.storage.connection import SQLiteConnectionManager
from src.modules.storage.repos.llm import LLMRepo
from src.modules.storage.schema import build_schema_sql

FAKE_CONFIG: Dict[str, Any] = {
    "llm_providers": [{"name": "fake", "client_type": "cacheprobe", "base_url": "fake://local", "api_key": "k"}],
    "llm_models": [{"name": "m1", "model_identifier": "probe-model", "api_provider": "fake"}],
    "llm_profiles": {"planner": {"model_list": ["m1"]}},
}

_OPENAI_STYLE = {"prompt_tokens_details": SimpleNamespace(cached_tokens=100)}
_DEEPSEEK_STYLE = {"prompt_cache_hit_tokens": 50, "prompt_cache_miss_tokens": 14}


class CacheProbeClient(BaseLLMClient):
    """厂商适配端替身：返回测试设定的 usage。"""

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self.usage_to_return: Optional[Usage] = None

    async def generate(self, request: GenerateRequest, *, model: str, **kwargs: Any) -> Response:
        return Response(success=True, content="fake 回复", usage=self.usage_to_return, model="probe-model")


@pytest.fixture
def conn_manager(tmp_path) -> SQLiteConnectionManager:
    manager = SQLiteConnectionManager(tmp_path / "test.db")
    manager.connection().executescript(build_schema_sql())
    return manager


def _cache_columns(conn_manager: SQLiteConnectionManager) -> list[tuple[int, int]]:
    with conn_manager.transaction() as conn:
        rows = conn.execute("SELECT cache_hit_tokens, cache_miss_tokens FROM llm_usage").fetchall()
    return [(r["cache_hit_tokens"], r["cache_miss_tokens"]) for r in rows]


@pytest.mark.parametrize(
    ("prompt", "fields", "expected"),
    [
        (100, _OPENAI_STYLE, {"cache_hit_tokens": 100, "cache_miss_tokens": 0}),
        # OpenAI 单字段：miss 按 max(prompt − hit, 0) 反推
        (200, _OPENAI_STYLE, {"cache_hit_tokens": 100, "cache_miss_tokens": 100}),
        # DeepSeek 风格同名直取，不走反推
        (64, _DEEPSEEK_STYLE, {"cache_hit_tokens": 50, "cache_miss_tokens": 14}),
        # 未上报：不含缓存键（下游映射为 None = 未上报）
        (10, {}, {}),
    ],
)
def test_extract_usage_normalizes_cache_fields(prompt: int, fields: dict, expected: dict) -> None:
    usage = OpenAIClient._extract_usage(
        SimpleNamespace(prompt_tokens=prompt, completion_tokens=5, total_tokens=prompt + 5, **fields)
    )
    assert usage == {"prompt_tokens": prompt, "completion_tokens": 5, "total_tokens": prompt + 5, **expected}
    assert OpenAIClient._extract_usage(None) is None


@pytest.mark.parametrize(
    ("prompt", "fields", "expected"), [(200, _OPENAI_STYLE, (100, 100)), (64, _DEEPSEEK_STYLE, (50, 14))]
)
async def test_record_usage_persists_extracted_cache(conn_manager, prompt: int, fields: dict, expected) -> None:
    extracted = OpenAIClient._extract_usage(
        SimpleNamespace(prompt_tokens=prompt, completion_tokens=8, total_tokens=prompt + 8, **fields)
    )
    await record_usage(
        LLMRepo(conn_manager), model_name="probe-model", provider_name="fake", request_type="generate", usage=extracted
    )
    assert _cache_columns(conn_manager) == [expected]


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (Usage(prompt_tokens=3, completion_tokens=5, total_tokens=8), (0, 0)),
        (
            Usage(prompt_tokens=90, completion_tokens=10, total_tokens=100, cache_hit_tokens=80, cache_miss_tokens=10),
            (80, 10),
        ),
    ],
    ids=["unreported", "reported"],
)
async def test_engine_persists_cache_columns(conn_manager, usage: Usage, expected) -> None:
    with patch.dict(_CLIENT_DISPATCH, {"cacheprobe": CacheProbeClient}):
        manager = LLMManager(llm_repo=LLMRepo(conn_manager))
        await manager.setup(FAKE_CONFIG)
    manager._provider_clients["fake"].usage_to_return = usage

    assert (await manager.generate("你好", profile="planner")).success is True
    assert _cache_columns(conn_manager) == [expected]


@pytest.mark.parametrize(
    ("cache", "cache_price", "prompt", "hit", "miss", "expected_cost_in"),
    [
        # cache=none：即使填了缓存价也按全价
        ("none", 0.5, 100, 80, 20, 100 * 1.0),
        # 双条件缺一（缓存价为 0）退全价，不按 0 计费
        ("hit_discount", 0.0, 100, 80, 20, 100 * 1.0),
        # 端点未上报缓存字段：miss 按 prompt 反推，退化为全价
        ("hit_discount", 0.1, 100, 0, 0, 100 * 1.0),
        # miss 报短：差额按全价补齐
        ("hit_discount", 0.1, 200, 100, 0, 100 * 0.1 + 100 * 1.0),
        ("hit_discount", 0.1, 100, 80, 20, 80 * 0.1 + 20 * 1.0),
        ("hit_discount", 0.1, 200, 100, 100, 100 * 0.1 + 100 * 1.0),
    ],
)
def test_calculate_cost_cache_segmentation(cache, cache_price, prompt, hit, miss, expected_cost_in) -> None:
    prices = {"m1": {"price_in": 1.0, "price_out": 2.0, "cache_price_in": cache_price, "cache": cache}}
    info = calculate_cost(
        prices, "m1", prompt_tokens=prompt, completion_tokens=50, cache_hit_tokens=hit, cache_miss_tokens=miss
    )
    assert info["cost_in"] == pytest.approx(expected_cost_in / 1e6)
    assert info["cost_out"] == pytest.approx(50 / 1e6 * 2.0)
    assert info["cost"] == pytest.approx(info["cost_in"] + info["cost_out"])

    unmatched = calculate_cost(prices, "unknown", prompt_tokens=100, completion_tokens=50)
    assert (unmatched["has_price"], unmatched["cost"]) == (False, 0.0)
