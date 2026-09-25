"""
memory/bootstrap.py 单元测试

覆盖：
- ``build_memory_stack``：
  - backend="simple" → 成功初始化 + 画像/事实表就绪
  - 未知 backend 值 → ``raise ValueError``
  - 缺省 / 异常输入（None / 非 dict / 缺 sqlite 段）→ 走默认值
- ``bind_memory_tools``：
  - 注册 query_memory / query_viewer_profile 双工具，能 invoke 查到已写入事实
  - registry 必须是 ``ToolRegistry``（type check）
  - memory 为 None → ``raise ValueError``
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator

import pytest

from src.modules.memory.bootstrap import (
    SUPPORTED_BACKENDS,
    bind_memory_tools,
    build_memory_stack,
)
from src.modules.memory.simple_memory import SimpleMemory
from src.modules.storage.database import SQLiteDatabase
from src.modules.tools import ToolInvocation, ToolRegistry


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tmp_db_dir(tmp_path: Path) -> Path:
    """临时目录用于 SQLite db 文件。"""
    db_dir = tmp_path / "memory_bootstrap"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir


@pytest.fixture
def simple_config(tmp_db_dir: Path) -> dict:
    """最小可用 config（backend=simple + sqlite.db_path 指向 tmp）。"""
    return {
        "memory": {"backend": "simple", "simple": None},
        "sqlite": {
            "db_path": str(tmp_db_dir / "test.db"),
            "wal": True,
            "busy_timeout_ms": 5000,
            "foreign_keys": True,
        },
    }


@pytest.fixture
async def built_stack(
    simple_config: dict,
) -> AsyncGenerator[tuple[SQLiteDatabase, SimpleMemory], None]:
    store, memory = await build_memory_stack(simple_config)
    yield store, memory
    await store.close()


# =============================================================================
# build_memory_stack
# =============================================================================


async def test_build_memory_stack_creates_tables(simple_config: dict) -> None:
    """backend=simple 成功初始化，画像/事实两张业务表就绪。"""
    store, memory = await build_memory_stack(simple_config)
    try:
        assert isinstance(store, SQLiteDatabase)
        assert isinstance(memory, SimpleMemory)
        assert await store.table_exists("viewer_facts")
        assert await store.table_exists("viewer_profiles")
    finally:
        await store.close()


async def test_build_memory_stack_idempotent(simple_config: dict) -> None:
    """重复构造指向同一库不报错（DDL 幂等）。"""
    store1, _ = await build_memory_stack(simple_config)
    try:
        store2, memory2 = await build_memory_stack(simple_config)
        try:
            assert await memory2.get_viewer_profile(platform="bilibili", user_id="nobody") is None
        finally:
            await store2.close()
    finally:
        await store1.close()


async def test_build_memory_stack_unknown_backend_raises(tmp_db_dir: Path) -> None:
    """未知 backend fail-fast（含类型异常值）。"""
    for bad in ("maibot", 123, None):
        config = {
            "memory": {"backend": bad},
            "sqlite": {"db_path": str(tmp_db_dir / "bad.db")},
        }
        with pytest.raises(ValueError):
            await build_memory_stack(config)


async def test_build_memory_stack_defaults_without_sqlite_section(
    tmp_db_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺 sqlite 段时走默认值。DEFAULT_DB_PATH 指向临时目录——真实默认库
    （data/amaidesu.db）绝不能被测试触碰。"""
    import src.modules.memory.bootstrap as bootstrap_module

    fake_default = tmp_db_dir / "default.db"
    monkeypatch.setattr(bootstrap_module, "DEFAULT_DB_PATH", fake_default)

    config = {"memory": {"backend": "simple"}}
    store, memory = await build_memory_stack(config)
    try:
        assert store.db_path == fake_default
        assert store.db_path.exists()
    finally:
        await store.close()


def test_supported_backends_covers_simple() -> None:
    assert SUPPORTED_BACKENDS == ("simple",)


# =============================================================================
# bind_memory_tools
# =============================================================================


async def test_bind_memory_tools_registers_both_tools(built_stack: tuple[SQLiteDatabase, SimpleMemory]) -> None:
    """注册 query_memory / query_viewer_profile 双工具。"""
    _store, memory = built_stack
    registry = ToolRegistry()
    added = bind_memory_tools(registry, memory)
    assert added == 2
    assert len(registry) == 2


async def test_bind_memory_tools_invoke_queries_facts(
    built_stack: tuple[SQLiteDatabase, SimpleMemory],
) -> None:
    """端到端：build → bind → 写事实 → invoke memory_query_memory 命中。"""
    _store, memory = built_stack
    await memory.add_viewer_fact(platform="bilibili", user_id="u_1", fact_text="弹幕互动很有趣，今天观众很多")

    registry = ToolRegistry()
    bind_memory_tools(registry, memory)
    res = await registry.invoke(
        ToolInvocation(tool_name="memory_query_memory", arguments={"query": "弹幕互动", "top_k": 3})
    )
    assert res.success is True
    assert "弹幕互动" in res.content


async def test_bind_memory_tools_duplicate_returns_zero(
    built_stack: tuple[SQLiteDatabase, SimpleMemory],
) -> None:
    """重复注册返回 0（不报错）。"""
    _store, memory = built_stack
    registry = ToolRegistry()
    assert bind_memory_tools(registry, memory) == 2
    assert bind_memory_tools(registry, memory) == 0


async def test_bind_memory_tools_type_checks(built_stack: tuple[SQLiteDatabase, SimpleMemory]) -> None:
    """registry 非 ToolRegistry / memory 为 None 都 fail-fast。"""
    _store, memory = built_stack
    with pytest.raises(TypeError):
        bind_memory_tools("not-a-registry", memory)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        bind_memory_tools(ToolRegistry(), None)  # type: ignore[arg-type]
