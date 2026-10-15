"""storage/bootstrap.py 单元测试

覆盖 ``build_database``：
- 读 ``[sqlite]`` 段构造库并完成建表（启动闸门表全部就位）
- 重复调用幂等（同一路径再次初始化不报错）
- 缺 sqlite 段走默认路径（默认路径被替换成临时目录，真实默认库绝不触碰）
- config 非 dict 抛 TypeError
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.modules.storage.bootstrap as bootstrap_module
from src.modules.storage.bootstrap import build_database
from src.modules.storage.schema import list_expected_tables


@pytest.fixture
def tmp_db_dir(tmp_path: Path) -> Path:
    db_dir = tmp_path / "storage_bootstrap"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir


@pytest.mark.asyncio
async def test_build_database_creates_tables(tmp_db_dir: Path) -> None:
    config = {"sqlite": {"db_path": str(tmp_db_dir / "test.db"), "busy_timeout_ms": 5000}}
    database = await build_database(config)
    try:
        assert database.db_path == tmp_db_dir / "test.db"
        for table in list_expected_tables():
            assert await database.table_exists(table), f"缺表 {table}"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_build_database_idempotent(tmp_db_dir: Path) -> None:
    config = {"sqlite": {"db_path": str(tmp_db_dir / "twice.db")}}
    first = await build_database(config)
    await first.close()
    second = await build_database(config)
    try:
        assert await second.table_exists("session_recaps")
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_build_database_defaults_without_sqlite_section(
    tmp_db_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_default = tmp_db_dir / "default.db"
    monkeypatch.setattr(bootstrap_module, "DEFAULT_DB_PATH", fake_default)
    database = await build_database({})
    try:
        assert database.db_path == fake_default
        assert fake_default.exists()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_build_database_rejects_non_dict() -> None:
    with pytest.raises(TypeError):
        await build_database(None)  # type: ignore[arg-type]
