"""
memory/bootstrap.py 单元测试（Wave 8 / 记忆接线修复）

覆盖：
- ``build_memory_stack``：
  - backend="simple" → 成功初始化 + 双表就绪
  - backend="amemorix" / 未知值 → ``raise ValueError``
  - 缺省 / 异常输入（None / 非 dict / 缺 storage.sqlite）→ 走默认值
- ``bind_memory_tools``：
  - 注册后 ``len(registry) + 1``，能 invoke query_memory 召回已写入事实
  - registry 必须是 ``ToolRegistry``（type check）
  - memory 为 None → ``raise ValueError``
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.memory.bootstrap import (
    SUPPORTED_BACKENDS,
    bind_memory_tools,
    build_memory_stack,
)
from src.modules.memory.simple_memory import SimpleMemory
from src.modules.storage.sqlite_store import SQLiteStore
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
    """最小可用 config（backend=simple + storage.sqlite.db_path 指向 tmp）。"""
    return {
        "memory": {"backend": "simple", "simple": None, "amemorix": None},
        "storage": {
            "sqlite": {
                "db_path": str(tmp_db_dir / "test.db"),
                "wal": True,
                "busy_timeout_ms": 5000,
                "foreign_keys": True,
            }
        },
    }


@pytest.fixture
async def built_stack(
    simple_config: dict,
) -> AsyncGenerator[tuple[SQLiteStore, SimpleMemory], None]:
    """构造完成的 (store, memory)；自动清理。"""
    store, mem = await build_memory_stack(simple_config)
    try:
        yield store, mem
    finally:
        await store.close()


# =============================================================================
# build_memory_stack：基本装配
# =============================================================================


async def test_build_memory_stack_returns_store_and_simple_memory(
    built_stack: tuple[SQLiteStore, SimpleMemory],
) -> None:
    """正常 config → 返回 (SQLiteStore, SimpleMemory) 且都已初始化。"""
    store, mem = built_stack
    assert isinstance(store, SQLiteStore)
    assert isinstance(mem, SimpleMemory)
    assert store.initialized is True
    assert mem._store is store  # SimpleMemory 持有同一 store


async def test_build_memory_stack_creates_private_tables(
    built_stack: tuple[SQLiteStore, SimpleMemory],
) -> None:
    """装配后 _memory_facts / _memory_profiles 私有表都已存在。"""
    store, _ = built_stack
    assert await store.table_exists("_memory_facts") is True
    assert await store.table_exists("_memory_profiles") is True


async def test_build_memory_stack_idempotent(tmp_db_dir: Path) -> None:
    """重复 build 不报错（initialize 幂等；同一 db_path 重复装配应可成功）。"""
    cfg = {
        "memory": {"backend": "simple"},
        "storage": {"sqlite": {"db_path": str(tmp_db_dir / "idem.db")}},
    }
    s1, m1 = await build_memory_stack(cfg)
    try:
        # 第二次 build 用同一 db_path（DDL 全部 IF NOT EXISTS，幂等）
        s2, m2 = await build_memory_stack(cfg)
        try:
            assert await s2.table_exists("_memory_facts")
            assert await s2.table_exists("_memory_profiles")
        finally:
            await s2.close()
    finally:
        await s1.close()


# =============================================================================
# build_memory_stack：backend fail-fast
# =============================================================================


async def test_build_memory_stack_rejects_amemorix_backend(tmp_db_dir: Path) -> None:
    """backend='amemorix' → ValueError（fail-fast，当前仅支持 simple）。"""
    cfg = {
        "memory": {"backend": "amemorix"},
        "storage": {"sqlite": {"db_path": str(tmp_db_dir / "amemorix.db")}},
    }
    with pytest.raises(ValueError) as exc_info:
        await build_memory_stack(cfg)
    msg = str(exc_info.value)
    assert "amemorix" in msg or "backend" in msg
    assert "simple" in msg  # 错误消息必须说明当前仅支持 simple


async def test_build_memory_stack_rejects_unknown_backend(tmp_db_dir: Path) -> None:
    """backend='unknown' → ValueError（任何非 SUPPORTED_BACKENDS 值都拒绝）。"""
    cfg = {
        "memory": {"backend": "redis"},
        "storage": {"sqlite": {"db_path": str(tmp_db_dir / "unknown.db")}},
    }
    with pytest.raises(ValueError):
        await build_memory_stack(cfg)


async def test_build_memory_stack_default_backend_is_simple(tmp_db_dir: Path) -> None:
    """不写 backend（缺省）→ 走 simple 默认（向后兼容）。"""
    cfg = {
        "memory": {},  # 无 backend 字段
        "storage": {"sqlite": {"db_path": str(tmp_db_dir / "default_backend.db")}},
    }
    store, mem = await build_memory_stack(cfg)
    try:
        assert isinstance(mem, SimpleMemory)
    finally:
        await store.close()


async def test_supported_backends_constant() -> None:
    """SUPPORTED_BACKENDS 当前只含 'simple'（任何变动需同步测试 + 文档）。"""
    assert "simple" in SUPPORTED_BACKENDS
    assert "amemorix" not in SUPPORTED_BACKENDS


# =============================================================================
# build_memory_stack：异常输入容忍
# =============================================================================


async def test_build_memory_stack_tolerates_missing_storage_block(tmp_db_dir: Path) -> None:
    """config['storage'] 缺省 / 非 dict → 走 DEFAULT_DB_PATH（不报错）。"""
    cfg = {"memory": {"backend": "simple"}}
    # storage 完全缺失：使用默认 DB 路径；为避免污染默认 DB 我们改用
    # 自定义 cfg 提供 sqlite 字段，但这里专门测 storage 缺失兜底
    store, mem = await build_memory_stack(cfg)
    try:
        assert isinstance(mem, SimpleMemory)
    finally:
        await store.close()


async def test_build_memory_stack_tolerates_non_dict_config() -> None:
    """config 不是 dict → TypeError（fail-fast，不静默走默认）。"""
    with pytest.raises(TypeError):
        await build_memory_stack("not a dict")  # type: ignore[arg-type]


async def test_build_memory_stack_resolves_relative_db_path_to_absolute(tmp_path: Path) -> None:
    """相对 db_path 必须解析为绝对路径（不依赖 cwd）。

    验证契约：
    - 输入相对路径（不带 ``/`` 前缀）
    - 输出 ``store.db_path.is_absolute() is True``
    - 解析结果含原始文件名（``rel_name``）

    不验证"解析到哪个目录根"——``_default_path.py`` 使用
    ``Path(__file__).resolve().parents[3]`` 定位项目根，在 git worktree 共享
    ``.git`` 的情况下会指向主工作区而非当前工作树；这是已知的解析策略，本测试
    只验证"相对路径 → 绝对路径"这一最小契约。
    """
    rel_name = "_rel_db_test_wave8.db"
    cfg = {
        "memory": {"backend": "simple"},
        "storage": {"sqlite": {"db_path": rel_name}},  # 相对路径
    }
    store, _ = await build_memory_stack(cfg)
    try:
        assert store.db_path.is_absolute(), "相对 db_path 必须被解析为绝对路径"
        assert store.db_path.name == rel_name, "文件名必须保留原 rel_name"
    finally:
        await store.close()
        # 清理：删除测试可能在某处生成的 db 文件（-wal/-shm/-journal 是 SQLite 副产物）
        for ext in ("", "-wal", "-shm", "-journal"):
            p = Path(store.db_path) if not ext else Path(str(store.db_path) + ext)
            if p.exists():
                p.unlink()


# =============================================================================
# bind_memory_tools：ToolRegistry 接线
# =============================================================================


async def test_bind_memory_tools_registers_query_memory(
    built_stack: tuple[SQLiteStore, SimpleMemory],
) -> None:
    """bind_memory_tools 后 registry 多 1 个工具（query_memory），能正常 invoke。"""
    _, mem = built_stack

    registry = ToolRegistry()
    new_count = bind_memory_tools(registry, mem)
    assert new_count == 1
    assert "query_memory" in registry
    assert len(registry) == 1


async def test_bind_memory_tools_invoke_recalls_ingested_fact(
    built_stack: tuple[SQLiteStore, SimpleMemory],
) -> None:
    """端到端：build → bind → ingest → invoke query_memory 召回中文事实。"""
    _, mem = built_stack
    await mem.ingest("弹幕互动很有趣，今天观众很多", source="seed", importance=10)

    registry = ToolRegistry()
    bind_memory_tools(registry, mem)

    # 用部分关键词触发召回（短 CJK 段取整段）
    res = await registry.invoke(
        ToolInvocation(tool_name="query_memory", arguments={"query": "弹幕互动", "top_k": 3})
    )
    assert res.success is True
    assert "弹幕" in res.content or "互动" in res.content
    assert "无匹配" not in res.content


async def test_bind_memory_tools_rejects_non_registry() -> None:
    """registry 必须是 ToolRegistry 实例；其他类型 → TypeError。"""
    with pytest.raises(TypeError):
        bind_memory_tools("not a registry", memory=None)  # type: ignore[arg-type]


async def test_bind_memory_tools_rejects_none_memory() -> None:
    """memory=None → ValueError（不允许出现"无 memory 的 query_memory 工具"）。"""
    registry = ToolRegistry()
    with pytest.raises(ValueError):
        bind_memory_tools(registry, memory=None)


async def test_bind_memory_tools_duplicate_returns_zero(built_stack: tuple[SQLiteStore, SimpleMemory]) -> None:
    """重复注册同一 memory（QueryMemoryToolProvider 实例不同但 spec.name 冲突）→ 返回 0。"""
    _, mem = built_stack
    registry = ToolRegistry()
    bind_memory_tools(registry, mem)
    new_count = bind_memory_tools(registry, mem)  # 第二次
    assert new_count == 0
    assert len(registry) == 1  # 没有重复添加