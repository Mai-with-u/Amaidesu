"""ViewerProfileRepo（观众画像仓储）单元测试

覆盖：upsert / get（含已整理时刻）/ 空文本拒写 / 列表分页搜索 / 人工纠正 / 删除 / 计数。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.repos import ViewerProfileRepo


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="viewer-profile-repo-"))
    yield td / "profiles.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    s = SQLiteDatabase(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
async def repo(store: SQLiteDatabase) -> ViewerProfileRepo:
    return store.viewer_profiles


@pytest.mark.asyncio
async def test_upsert_and_get(repo: ViewerProfileRepo) -> None:
    assert await repo.upsert_profile(
        platform="bilibili", user_id="u1", profile_text="老粉，爱聊数码", last_compressed_at_ms=100
    )
    assert await repo.get_profile_text(platform="bilibili", user_id="u1") == "老粉，爱聊数码"
    row = await repo.get_profile(platform="bilibili", user_id="u1")
    assert row is not None
    assert int(row["last_compressed_at_ms"]) == 100

    # 覆盖写：文本与已整理时刻一起推进
    assert await repo.upsert_profile(
        platform="bilibili", user_id="u1", profile_text="老粉，最近在考试", last_compressed_at_ms=200
    )
    row = await repo.get_profile(platform="bilibili", user_id="u1")
    assert row is not None
    assert row["profile_text"] == "老粉，最近在考试"
    assert int(row["last_compressed_at_ms"]) == 200


@pytest.mark.asyncio
async def test_upsert_rejects_empty_text_and_identity(repo: ViewerProfileRepo) -> None:
    assert (
        await repo.upsert_profile(platform="bilibili", user_id="u1", profile_text="   ", last_compressed_at_ms=1)
        is False
    )
    assert await repo.upsert_profile(platform="", user_id="u1", profile_text="x", last_compressed_at_ms=1) is False
    assert await repo.get_profile_text(platform="bilibili", user_id="u1") is None


@pytest.mark.asyncio
async def test_list_search_and_pagination(repo: ViewerProfileRepo) -> None:
    for i in range(5):
        text = f"印象{i}：喜欢Minecraft" if i % 2 else f"印象{i}：喜欢恐怖游戏"
        await repo.upsert_profile(platform="bilibili", user_id=f"u_{i}", profile_text=text, last_compressed_at_ms=i)
    total, rows = await repo.list_profiles(limit=2, offset=0)
    assert total == 5 and len(rows) == 2
    total, rows = await repo.list_profiles(search="Minecraft")
    assert total == 2
    assert all("Minecraft" in r["profile_text"] for r in rows)
    total, rows = await repo.list_profiles(search="u_3")
    assert total == 1 and rows[0]["user_id"] == "u_3"
    assert await repo.count_profiles() == 5


@pytest.mark.asyncio
async def test_update_text_and_delete(repo: ViewerProfileRepo) -> None:
    await repo.upsert_profile(platform="bilibili", user_id="u1", profile_text="初版", last_compressed_at_ms=10)
    assert await repo.update_profile_text(platform="bilibili", user_id="u1", profile_text="人工纠正版")
    row = await repo.get_profile(platform="bilibili", user_id="u1")
    assert row is not None
    assert row["profile_text"] == "人工纠正版"
    assert int(row["last_compressed_at_ms"]) == 10  # 纠正不改已整理时刻

    assert await repo.update_profile_text(platform="bilibili", user_id="u1", profile_text="  ") is False
    assert await repo.update_profile_text(platform="bilibili", user_id="ghost", profile_text="x") is False
    assert await repo.delete_profile(platform="bilibili", user_id="u1") is True
    assert await repo.delete_profile(platform="bilibili", user_id="u1") is False
    assert await repo.get_profile_text(platform="bilibili", user_id="u1") is None
