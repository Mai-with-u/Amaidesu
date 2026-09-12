"""
llm_usage 落库链路单测

覆盖：
- insert_llm_usage 往返：字段完整落列、可选字段默认、毫秒时间戳
- LLMManager 成功调用后旁路写 llm_usage（注入 store 时 +1 行；未注入不落库）
- 落库失败只降级记日志，不阻断 LLM 调用链
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.llm.manager import LLMManager, LLMResponse
from src.modules.storage.sqlite_store import SQLiteStore


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="storage-llm-usage-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteStore, None]:
    s = SQLiteStore(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


# ===== insert_llm_usage =====


@pytest.mark.asyncio
async def test_insert_llm_usage_roundtrip(store: SQLiteStore) -> None:
    rowid = await store.insert_llm_usage(
        model_name="glm-4.7",
        provider_name="zhipu",
        request_type="chat",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cache_hit_tokens=80,
        cache_miss_tokens=20,
        cost=0.0123,
        duration_ms=850,
        profile_name="llm",
        timestamp_ms=1_700_000_000_000,
    )
    assert rowid > 0
    rows = await store.execute("SELECT * FROM llm_usage WHERE id=?", (rowid,))
    assert len(rows) == 1
    row = rows[0]
    assert row["model_name"] == "glm-4.7"
    assert row["provider_name"] == "zhipu"
    assert row["profile_name"] == "llm"
    assert row["request_type"] == "chat"
    assert row["prompt_tokens"] == 100
    assert row["total_tokens"] == 150
    assert row["cache_hit_tokens"] == 80
    assert row["cache_miss_tokens"] == 20
    assert row["cost"] == pytest.approx(0.0123)
    assert row["duration_ms"] == 850
    assert row["timestamp_ms"] == 1_700_000_000_000
    # 可选字段默认 NULL；毫秒命名硬规则
    assert row["assign_name"] is None
    assert row["live_session_id"] is None


# ===== LLMManager 旁路落库 =====


class _FakeUsageClient:
    """伪客户端：chat 成功并返回 usage（不发起网络请求）。"""

    async def chat(self, messages, **kwargs):
        return LLMResponse(
            success=True,
            content="回复",
            model="glm-4.7",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )


def _make_manager_with_fake_client(store: SQLiteStore, monkeypatch) -> LLMManager:
    manager = LLMManager(sqlite_store=store)
    # 直接注入客户端与配置，绕过 setup() 的真实 provider 装配
    fake_client = _FakeUsageClient()
    manager._provider_clients["zhipu"] = fake_client
    manager._providers["zhipu"] = ({"name": "zhipu", "client_type": "openai"}, fake_client)
    manager._models["glm-4.7"] = (
        {"name": "glm-4.7", "model_identifier": "glm-4.7", "api_provider": "zhipu"},
        "zhipu",
    )
    from src.modules.llm.manager import _ResolvedModel, _ResolvedProfile

    manager._profiles["llm"] = _ResolvedProfile(
        profile_name="llm",
        hard_timeout_ms=90_000,
        slow_threshold_ms=15_000,
        selection_strategy="sequential",
        seed=0,
        temperature=0.3,
        max_tokens=4096,
        models=[
            _ResolvedModel(
                model_name="glm-4.7", model_identifier="glm-4.7", provider_name="zhipu"
            )
        ],
    )
    manager._model_call_counts["llm"] = {}
    return manager


@pytest.mark.asyncio
async def test_successful_call_persists_llm_usage(store: SQLiteStore, monkeypatch) -> None:
    manager = _make_manager_with_fake_client(store, monkeypatch)
    result = await manager.chat("你好", client_type="llm")
    assert result.success

    rows = await store.execute("SELECT * FROM llm_usage")
    assert len(rows) == 1
    row = rows[0]
    assert row["model_name"] == "glm-4.7"
    assert row["provider_name"] == "zhipu"
    assert row["profile_name"] == "llm"
    assert row["prompt_tokens"] == 10
    assert row["total_tokens"] == 15
    assert row["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_call_without_store_does_not_persist(monkeypatch) -> None:
    manager = LLMManager()  # 未注入 store：不落库也不报错
    fake_client = _FakeUsageClient()
    manager._provider_clients["zhipu"] = fake_client
    manager._providers["zhipu"] = ({"name": "zhipu", "client_type": "openai"}, fake_client)
    manager._models["glm-4.7"] = (
        {"name": "glm-4.7", "model_identifier": "glm-4.7", "api_provider": "zhipu"},
        "zhipu",
    )
    from src.modules.llm.manager import _ResolvedModel, _ResolvedProfile

    manager._profiles["llm"] = _ResolvedProfile(
        profile_name="llm",
        hard_timeout_ms=90_000,
        slow_threshold_ms=15_000,
        selection_strategy="sequential",
        seed=0,
        temperature=0.3,
        max_tokens=4096,
        models=[
            _ResolvedModel(
                model_name="glm-4.7", model_identifier="glm-4.7", provider_name="zhipu"
            )
        ],
    )
    manager._model_call_counts["llm"] = {}
    result = await manager.chat("你好", client_type="llm")
    assert result.success


@pytest.mark.asyncio
async def test_persist_failure_degrades_without_breaking_call(store: SQLiteStore, monkeypatch) -> None:
    manager = _make_manager_with_fake_client(store, monkeypatch)

    async def _boom(**kwargs):
        raise RuntimeError("db locked")

    monkeypatch.setattr(store, "insert_llm_usage", _boom)
    result = await manager.chat("你好", client_type="llm")
    # 落库失败不阻断调用链，调用仍成功返回
    assert result.success
