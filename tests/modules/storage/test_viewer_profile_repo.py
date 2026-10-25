"""ViewerProfileRepo（观众画像仓储）单元测试

覆盖：upsert / get（含已整理时刻）/ 空文本拒写 / 未建档观众拒写 / 列表分页搜索 / 人工纠正 / 删除 / 计数。
画像以内部观众 ID 为键；用例先给用到的观众建档，拿到观众 ID 再读写。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Dict, Generator

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.repos import ViewerProfileRepo
from tests.modules.storage.viewer_seed import seed_viewer


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
async def vids(store: SQLiteDatabase) -> Dict[str, int]:
    """为用例里出现的观众建档，返回 外部 ID → 内部观众 ID。"""
    ids: Dict[str, int] = {}
    for user_id in ("u1", *(f"u_{i}" for i in range(5))):
        ids[user_id] = await seed_viewer(
            store.viewers, platform="bilibili", user_id=user_id, user_name=f"观众{user_id}", timestamp_ms=1
        )
    return ids


@pytest.fixture
async def repo(store: SQLiteDatabase) -> ViewerProfileRepo:
    return store.viewer_profiles


@pytest.mark.asyncio
async def test_upsert_and_get(repo: ViewerProfileRepo, vids: Dict[str, int]) -> None:
    vid = vids["u1"]
    assert await repo.upsert_profile(viewer_id=vid, profile_text="老粉，爱聊数码", last_compressed_at_ms=100)
    assert await repo.get_profile_text(viewer_id=vid) == "老粉，爱聊数码"
    row = await repo.get_profile(viewer_id=vid)
    assert row is not None
    assert (int(row["last_compressed_at_ms"]), row["user_name"], row["platform"]) == (100, "观众u1", "bilibili")

    # 覆盖写：文本与已整理时刻一起推进
    assert await repo.upsert_profile(viewer_id=vid, profile_text="老粉，最近在考试", last_compressed_at_ms=200)
    row = await repo.get_profile(viewer_id=vid)
    assert row is not None
    assert row["profile_text"] == "老粉，最近在考试"
    assert int(row["last_compressed_at_ms"]) == 200


@pytest.mark.asyncio
async def test_upsert_rejects_empty_text_and_unknown_viewer(repo: ViewerProfileRepo, vids: Dict[str, int]) -> None:
    assert await repo.upsert_profile(viewer_id=vids["u1"], profile_text="   ", last_compressed_at_ms=1) is False
    # 不存在的观众没有画像可挂
    assert await repo.upsert_profile(viewer_id=9999, profile_text="x", last_compressed_at_ms=1) is False
    assert await repo.get_profile_text(viewer_id=vids["u1"]) is None


@pytest.mark.asyncio
async def test_list_search_and_pagination(repo: ViewerProfileRepo, vids: Dict[str, int]) -> None:
    for i in range(5):
        text = f"印象{i}：喜欢Minecraft" if i % 2 else f"印象{i}：喜欢恐怖游戏"
        await repo.upsert_profile(viewer_id=vids[f"u_{i}"], profile_text=text, last_compressed_at_ms=i)
    total, rows = await repo.list_profiles(limit=2, offset=0)
    assert total == 5 and len(rows) == 2
    total, rows = await repo.list_profiles(search="Minecraft")
    assert total == 2
    assert all("Minecraft" in r["profile_text"] for r in rows)
    # 也能按观众当前昵称搜
    total, rows = await repo.list_profiles(search="观众u_3")
    assert total == 1 and rows[0]["viewer_id"] == vids["u_3"]
    assert await repo.count_profiles() == 5


@pytest.mark.asyncio
async def test_update_text_and_delete(repo: ViewerProfileRepo, vids: Dict[str, int]) -> None:
    vid = vids["u1"]
    await repo.upsert_profile(viewer_id=vid, profile_text="初版", last_compressed_at_ms=10)
    assert await repo.update_profile_text(viewer_id=vid, profile_text="人工纠正版")
    row = await repo.get_profile(viewer_id=vid)
    assert row is not None
    assert row["profile_text"] == "人工纠正版"
    assert int(row["last_compressed_at_ms"]) == 10  # 纠正不改已整理时刻

    assert await repo.update_profile_text(viewer_id=vid, profile_text="  ") is False
    assert await repo.update_profile_text(viewer_id=9999, profile_text="x") is False
    assert await repo.delete_profile(viewer_id=vid) is True
    assert await repo.delete_profile(viewer_id=vid) is False
    assert await repo.get_profile_text(viewer_id=vid) is None
