"""sim_personas / sim_gifts 表 CRUD 与启动期种子导入测试。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.simulator.seed_data import (
    DEFAULT_GIFTS,
    DEFAULT_PERSONAS,
    seed_simulator_data,
)
from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="sim-tables-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path):  # noqa: ANN201 仓储实例
    db = SQLiteDatabase(temp_db_path)
    await db.initialize()
    yield db.sim
    await db.close()


# =============================================================================
# sim_personas CRUD
# =============================================================================


@pytest.mark.asyncio
async def test_persona_insert_and_list(store: SQLiteDatabase) -> None:
    """插入后人设可列出；停用行默认不可见。"""
    await store.insert_sim_persona(
        user_id="sim_a",
        user_nickname="观众甲",
        role="fan",
        personality="热情",
        speaking_style="活泼",
    )
    await store.insert_sim_persona(
        user_id="sim_b",
        user_nickname="观众乙",
        role="veteran",
        personality="老练",
        speaking_style="随意",
        is_active=False,
    )

    visible = await store.list_sim_personas()
    assert [row["user_id"] for row in visible] == ["sim_a"]

    all_rows = await store.list_sim_personas(include_inactive=True)
    assert {row["user_id"] for row in all_rows} == {"sim_a", "sim_b"}


@pytest.mark.asyncio
async def test_persona_duplicate_user_id_rejected(store: SQLiteDatabase) -> None:
    """user_id 唯一约束生效。"""
    await store.insert_sim_persona(
        user_id="sim_dup",
        user_nickname="第一次",
        role="fan",
        personality="p",
        speaking_style="s",
    )
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        await store.insert_sim_persona(
            user_id="sim_dup",
            user_nickname="第二次",
            role="fan",
            personality="p",
            speaking_style="s",
        )


@pytest.mark.asyncio
async def test_persona_update_whitelist_and_timestamp(store: SQLiteDatabase) -> None:
    """白名单外字段拒绝；合法更新维护 updated_at_ms。"""
    await store.insert_sim_persona(
        user_id="sim_u",
        user_nickname="旧名",
        role="fan",
        personality="旧性格",
        speaking_style="旧风格",
    )
    before = (await store.list_sim_personas(include_inactive=True))[0]

    updated = await store.update_sim_persona(user_id="sim_u", fields={"user_nickname": "新名"})
    assert updated is True
    row = (await store.list_sim_personas(include_inactive=True))[0]
    assert row["user_nickname"] == "新名"
    assert row["updated_at_ms"] >= before["updated_at_ms"]

    with pytest.raises(ValueError, match="非法字段"):
        await store.update_sim_persona(user_id="sim_u", fields={"user_id": "hack"})


@pytest.mark.asyncio
async def test_persona_update_nonexistent_returns_false(store: SQLiteDatabase) -> None:
    """更新不存在的 user_id 返回 False。"""
    updated = await store.update_sim_persona(user_id="ghost", fields={"personality": "x"})
    assert updated is False


@pytest.mark.asyncio
async def test_persona_delete(store: SQLiteDatabase) -> None:
    """删除存在的人设返回 True，再删返回 False。"""
    await store.insert_sim_persona(
        user_id="sim_d",
        user_nickname="待删",
        role="fan",
        personality="p",
        speaking_style="s",
    )
    assert await store.delete_sim_persona(user_id="sim_d") is True
    assert await store.delete_sim_persona(user_id="sim_d") is False
    assert await store.count_sim_personas() == 0


# =============================================================================
# sim_gifts CRUD
# =============================================================================


@pytest.mark.asyncio
async def test_gift_insert_list_update_delete(store: SQLiteDatabase) -> None:
    """礼物目录增查改删全链。"""
    await store.insert_sim_gift(
        gift_id="g1",
        gift_name="辣条",
        category="normal",
        weight=10,
        data_type="gift",
    )
    await store.insert_sim_gift(
        gift_id="sc1",
        gift_name="SC 50元",
        category="sc",
        weight=1,
        data_type="super_chat",
        sc_amount_rmb=50,
    )

    rows = await store.list_sim_gifts()
    assert len(rows) == 2
    sc_row = next(r for r in rows if r["gift_id"] == "sc1")
    assert sc_row["sc_amount_rmb"] == 50

    assert await store.update_sim_gift(gift_id="g1", fields={"weight": 20}) is True
    row = (await store.list_sim_gifts())[0]
    assert row["weight"] == 20

    with pytest.raises(ValueError, match="非法字段"):
        await store.update_sim_gift(gift_id="g1", fields={"gift_id": "hack"})

    assert await store.delete_sim_gift(gift_id="g1") is True
    assert await store.count_sim_gifts() == 1


# =============================================================================
# 启动期种子导入
# =============================================================================


@pytest.mark.asyncio
async def test_seed_imports_into_empty_tables(store: SQLiteDatabase) -> None:
    """空表导入内置默认值，数量与常量一致。"""
    await seed_simulator_data(store)
    assert await store.count_sim_personas() == len(DEFAULT_PERSONAS)
    assert await store.count_sim_gifts() == len(DEFAULT_GIFTS)

    personas = {row["user_id"] for row in await store.list_sim_personas()}
    assert personas == {p["user_id"] for p in DEFAULT_PERSONAS}


@pytest.mark.asyncio
async def test_seed_is_idempotent(store: SQLiteDatabase) -> None:
    """非空表重复 seed 不追加（幂等）。"""
    await seed_simulator_data(store)
    await seed_simulator_data(store)
    assert await store.count_sim_personas() == len(DEFAULT_PERSONAS)
    assert await store.count_sim_gifts() == len(DEFAULT_GIFTS)


@pytest.mark.asyncio
async def test_seed_skips_existing_user_data(store: SQLiteDatabase) -> None:
    """用户已有数据（哪怕只剩一行）不被种子覆盖。"""
    await store.insert_sim_persona(
        user_id="my_own",
        user_nickname="自建人设",
        role="fan",
        personality="p",
        speaking_style="s",
    )
    await seed_simulator_data(store)
    rows = await store.list_sim_personas()
    assert [row["user_id"] for row in rows] == ["my_own"]
