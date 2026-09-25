"""缓存 token 记账链路测试

覆盖两段链路：

- OpenAIClient 把厂商响应的缓存字段归一化进 usage
  （OpenAI 风格 cached_tokens / DeepSeek 风格 prompt_cache_hit/miss_tokens）
- Engine 落库统一走 observation.record_usage，缓存列写入现有
  ``llm_usage`` 表（未上报落 0），断言走真实 SQLite（tmp_path 建库）
- observation.calculate_cost 按价格表 cache/cache_price_in 启用分段计价
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional
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
    "llm_providers": [
        {"name": "fake", "client_type": "cacheprobe", "base_url": "fake://local", "api_key": "k"},
    ],
    "llm_models": [
        {"name": "m1", "model_identifier": "probe-model", "api_provider": "fake"},
    ],
    "llm_profiles": {
        "planner": {"model_list": ["m1"], "temperature": 0.7, "max_tokens": 4096},
    },
}


class CacheProbeClient(BaseLLMClient):
    """fake 厂商适配端：按测试设定返回带缓存字段的 usage"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.usage_to_return: Optional[Usage] = None

    async def generate(
        self,
        request: GenerateRequest,
        *,
        model: str,
        temperature: Optional[float] = None,
        reasoning_effort: Optional[str] = None,
        on_delta: Any = None,
        interrupt_flag: Any = None,
    ) -> Response:
        return Response(success=True, content="fake 回复", usage=self.usage_to_return, model="probe-model")


@pytest.fixture
def usage_env(tmp_path):
    """真实 SQLite（tmp_path）+ 注入 LLMRepo 的 LLMManager

    yields (manager, repo, conn_manager)；测试经 conn_manager 直接查
    ``llm_usage`` 表断言落库行。
    """
    conn_manager = SQLiteConnectionManager(tmp_path / "test.db")
    conn_manager.connection().executescript(build_schema_sql())
    repo = LLMRepo(conn_manager)
    with patch.dict(_CLIENT_DISPATCH, {"cacheprobe": CacheProbeClient}):
        manager = LLMManager(llm_repo=repo)
        yield manager, repo, conn_manager
    # 连接管理器无显式 close；tmp_path 数据库随用例结束自动清理


async def _fetch_usage_rows(conn_manager: SQLiteConnectionManager) -> List[Any]:
    with conn_manager.transaction() as conn:
        return conn.execute("SELECT * FROM llm_usage").fetchall()


class TestExtractUsage:
    """Client 侧提取归一化（OpenAI 风格 / DeepSeek 风格 / 未上报）"""

    def test_openai_style_cached_tokens(self):
        """OpenAI 风格 prompt_tokens_details.cached_tokens → cache_hit_tokens；miss 按 prompt − hit 反推"""
        vendor_usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            prompt_tokens_details=SimpleNamespace(cached_tokens=100),
        )
        usage = OpenAIClient._extract_usage(vendor_usage)
        assert usage is not None
        assert usage["cache_hit_tokens"] == 100
        assert usage["cache_miss_tokens"] == 0

    def test_openai_style_cached_infers_miss_when_prompt_larger(self):
        """OpenAI 单字段：prompt > hit → miss = max(prompt − hit, 0) 反推填充"""
        vendor_usage = SimpleNamespace(
            prompt_tokens=200,
            completion_tokens=30,
            total_tokens=230,
            prompt_tokens_details=SimpleNamespace(cached_tokens=100),
        )
        usage = OpenAIClient._extract_usage(vendor_usage)
        assert usage is not None
        assert usage["cache_hit_tokens"] == 100
        assert usage["cache_miss_tokens"] == 100

    def test_deepseek_style_hit_and_miss(self):
        """DeepSeek 风格 prompt_cache_hit/miss_tokens 同名直取，不走 OpenAI 反推"""
        vendor_usage = SimpleNamespace(
            prompt_tokens=64,
            completion_tokens=8,
            total_tokens=72,
            prompt_cache_hit_tokens=50,
            prompt_cache_miss_tokens=14,
        )
        usage = OpenAIClient._extract_usage(vendor_usage)
        assert usage is not None
        assert usage["cache_hit_tokens"] == 50
        assert usage["cache_miss_tokens"] == 14

    def test_no_cache_reported_omits_keys(self):
        """未上报缓存时 dict 不含缓存键（下游转 Usage 映射为 None = 未上报）"""
        vendor_usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        usage = OpenAIClient._extract_usage(vendor_usage)
        assert usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    def test_none_usage_returns_none(self):
        assert OpenAIClient._extract_usage(None) is None


class TestCacheAccountingThroughLedger:
    """observation.record_usage → llm_usage 表（真实 SQLite）"""

    async def test_openai_cached_tokens(self, usage_env):
        """OpenAI 风格提取结果经 record_usage 落库 cache_hit_tokens == 100；miss 按反推填 100"""
        manager, _, conn_manager = usage_env
        await manager.setup(FAKE_CONFIG)
        extracted = OpenAIClient._extract_usage(
            SimpleNamespace(
                prompt_tokens=200,
                completion_tokens=30,
                total_tokens=230,
                prompt_tokens_details=SimpleNamespace(cached_tokens=100),
            )
        )
        await record_usage(
            LLMRepo(conn_manager),
            model_name="probe-model",
            provider_name="fake",
            request_type="generate",
            usage=extracted,
        )
        rows = await _fetch_usage_rows(conn_manager)
        assert len(rows) == 1
        assert rows[0]["cache_hit_tokens"] == 100
        assert rows[0]["cache_miss_tokens"] == 100

    async def test_deepseek_style_hit_and_miss_persisted(self, usage_env):
        """DeepSeek 风格 hit/miss 各落各列"""
        manager, _, conn_manager = usage_env
        await manager.setup(FAKE_CONFIG)
        extracted = OpenAIClient._extract_usage(
            SimpleNamespace(
                prompt_tokens=64,
                completion_tokens=8,
                total_tokens=72,
                prompt_cache_hit_tokens=50,
                prompt_cache_miss_tokens=14,
            )
        )
        await record_usage(
            LLMRepo(conn_manager),
            model_name="probe-model",
            provider_name="fake",
            request_type="generate",
            usage=extracted,
        )
        rows = await _fetch_usage_rows(conn_manager)
        assert rows[0]["cache_hit_tokens"] == 50
        assert rows[0]["cache_miss_tokens"] == 14

    async def test_unreported_cache_persisted_as_zero(self, usage_env):
        """provider 未上报缓存 → 落 0 且不报错"""
        manager, _, conn_manager = usage_env
        await manager.setup(FAKE_CONFIG)
        probe = manager._provider_clients["fake"]
        probe.usage_to_return = Usage(prompt_tokens=3, completion_tokens=5, total_tokens=8)

        resp = await manager.generate("你好", profile="planner")

        assert resp.success is True
        rows = await _fetch_usage_rows(conn_manager)
        assert len(rows) == 1
        assert rows[0]["cache_hit_tokens"] == 0
        assert rows[0]["cache_miss_tokens"] == 0

    async def test_reported_cache_via_engine_persisted(self, usage_env):
        """fake 厂商上报缓存字段 → Engine 落库链路带出缓存列"""
        manager, _, conn_manager = usage_env
        await manager.setup(FAKE_CONFIG)
        probe = manager._provider_clients["fake"]
        probe.usage_to_return = Usage(
            prompt_tokens=90, completion_tokens=10, total_tokens=100, cache_hit_tokens=80, cache_miss_tokens=10
        )

        await manager.generate("缓存测试", profile="planner")

        rows = await _fetch_usage_rows(conn_manager)
        assert rows[0]["cache_hit_tokens"] == 80
        assert rows[0]["cache_miss_tokens"] == 10


class TestCalculateCostCacheSegmentation:
    """observation.calculate_cost 缓存命中按 cache_price_in 分段计价（修复既有缺陷）"""

    @staticmethod
    def _prices(*, cache_type: str = "", cache_price_in: float = 0.0) -> Dict[str, Dict[str, Any]]:
        return {
            "m1": {
                "price_in": 1.0,
                "price_out": 2.0,
                "cache_price_in": cache_price_in,
                "cache": cache_type,
            }
        }

    def test_full_price_when_cache_type_empty(self):
        """cache 类型空 → 全价不变（既有行为，缓存字段被忽略）"""
        prices = self._prices(cache_type="", cache_price_in=0.5)
        info = calculate_cost(
            prices, "m1", prompt_tokens=100, completion_tokens=50, cache_hit_tokens=80, cache_miss_tokens=20
        )
        assert info["cost_in"] == pytest.approx(100 / 1e6 * 1.0)
        assert info["cost_out"] == pytest.approx(50 / 1e6 * 2.0)
        assert info["cost"] == pytest.approx(info["cost_in"] + info["cost_out"])

    def test_full_price_when_cache_price_zero(self):
        """cache_price_in=0（即使声明了缓存类型）→ 走全价，避免 0 缓存价按 0 计费"""
        prices = self._prices(cache_type="anthropic", cache_price_in=0.0)
        info = calculate_cost(
            prices, "m1", prompt_tokens=100, completion_tokens=50, cache_hit_tokens=80, cache_miss_tokens=20
        )
        assert info["cost_in"] == pytest.approx(100 / 1e6 * 1.0)
        assert info["cost_out"] == pytest.approx(50 / 1e6 * 2.0)

    def test_segmented_when_cache_type_and_price(self):
        """cache 非空 + cache_price_in > 0 → 分段计价：hit × cache_price_in + miss × price_in"""
        prices = self._prices(cache_type="anthropic", cache_price_in=0.1)
        info = calculate_cost(
            prices, "m1", prompt_tokens=100, completion_tokens=50, cache_hit_tokens=80, cache_miss_tokens=20
        )
        assert info["cost_in"] == pytest.approx(80 / 1e6 * 0.1 + 20 / 1e6 * 1.0)
        assert info["cost_out"] == pytest.approx(50 / 1e6 * 2.0)
        assert info["cost"] == pytest.approx(info["cost_in"] + info["cost_out"])

    def test_segmented_after_openai_style_miss_inference(self):
        """OpenAI 单字段：Client 反推 miss 后（prompt=200, hit=100, miss=100）→ 分段计价"""
        prices = self._prices(cache_type="anthropic", cache_price_in=0.1)
        info = calculate_cost(
            prices, "m1", prompt_tokens=200, completion_tokens=50, cache_hit_tokens=100, cache_miss_tokens=100
        )
        assert info["cost_in"] == pytest.approx(100 / 1e6 * 0.1 + 100 / 1e6 * 1.0)
        assert info["cost_out"] == pytest.approx(50 / 1e6 * 2.0)

    def test_missing_model_price_returns_unmatched(self):
        """模型不在价格表 → 维持既有未匹配返回（不抛错）"""
        info = calculate_cost(
            self._prices(cache_type="anthropic", cache_price_in=0.1),
            "unknown",
            prompt_tokens=100,
            completion_tokens=50,
            cache_hit_tokens=80,
            cache_miss_tokens=20,
        )
        assert info["has_price"] is False
        assert info["cost"] == 0.0
