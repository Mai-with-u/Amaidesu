"""
SimpleMemory（观众事实 / 画像读写服务）单元测试

覆盖：
- 事实面：add_viewer_fact 写入 / 同观众同文本去重 / 按人查 / 关键词召回 /
  水位查询 / 单条删除
- 画像面：upsert / get（含水位）/ 列表分页搜索 / 人工纠正 / 删除
- 画像生成输入：list_profile_candidates 的水位过滤与互动门槛
- query_memory / query_viewer_profile 工具：注册入 ToolRegistry 后 invoke
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.memory import SimpleMemory
from src.modules.memory.query_tool import build_memory_tools
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.repos import ViewerRepo
from src.modules.tools import ToolInvocation, ToolRegistry


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="w3-memory-"))
    yield td / "memory.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    s = SQLiteDatabase(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
async def viewer_repo(store: SQLiteDatabase) -> ViewerRepo:
    return ViewerRepo(store.manager)


@pytest.fixture
async def memory(store: SQLiteDatabase) -> AsyncGenerator[SimpleMemory, None]:
    mem = SimpleMemory(store)
    await mem.initialize()
    yield mem


async def _seed_viewer(
    viewer_repo: ViewerRepo,
    *,
    platform: str = "bilibili",
    user_id: str = "u_1",
    user_name: str = "老观众",
    interaction_count: int = 0,
) -> None:
    """造一行 viewers 统计（门槛过滤用）。"""
    await viewer_repo.upsert_viewer_message(platform=platform, user_id=user_id, user_name=user_name, timestamp_ms=1_000)
    for _ in range(max(0, interaction_count - 1)):
        await viewer_repo.upsert_viewer_message(
            platform=platform, user_id=user_id, user_name=user_name, timestamp_ms=2_000
        )


# =============================================================================
# 事实面
# =============================================================================


async def test_add_fact_and_list_by_viewer(memory: SimpleMemory) -> None:
    """写入后按人可查，时间倒序。"""
    assert await memory.add_viewer_fact(platform="bilibili", user_id="u_1", fact_text="喜欢玩 Minecraft") is True
    assert await memory.add_viewer_fact(platform="bilibili", user_id="u_1", fact_text="下周要考试") is True
    facts = await memory.list_viewer_facts(platform="bilibili", user_id="u_1")
    assert len(facts) == 2
    assert facts[0].fact_text == "下周要考试"  # 后写入在前
    assert facts[0].fact_id > facts[1].fact_id


async def test_add_fact_dedupes_same_viewer_same_text(memory: SimpleMemory) -> None:
    """同观众同文本去重；不同观众同文本不去重。"""
    assert await memory.add_viewer_fact(platform="bilibili", user_id="u_1", fact_text="喜欢猫咪") is True
    assert await memory.add_viewer_fact(platform="bilibili", user_id="u_1", fact_text="喜欢猫咪") is False
    assert await memory.add_viewer_fact(platform="bilibili", user_id="u_2", fact_text="喜欢猫咪") is True
    assert len(await memory.list_viewer_facts(platform="bilibili", user_id="u_1")) == 1
    assert len(await memory.list_viewer_facts(platform="bilibili", user_id="u_2")) == 1


async def test_add_fact_rejects_empty_or_missing_identity(memory: SimpleMemory) -> None:
    """空文本 / 缺身份键被拒绝。"""
    assert await memory.add_viewer_fact(platform="bilibili", user_id="u_1", fact_text="   ") is False
    assert await memory.add_viewer_fact(platform="", user_id="u_1", fact_text="x") is False
    assert await memory.add_viewer_fact(platform="bilibili", user_id="", fact_text="x") is False


async def test_search_facts_by_keyword(memory: SimpleMemory) -> None:
    """关键词召回事实文本（query_memory 数据面）。"""
    await memory.add_viewer_fact(platform="bilibili", user_id="u_1", fact_text="下周要考试，直播会少来")
    await memory.add_viewer_fact(platform="bilibili", user_id="u_2", fact_text="喜欢 Minecraft 建筑玩法")
    hits = await memory.search_viewer_facts(query="考试", top_k=5)
    assert len(hits) == 1
    assert hits[0].user_id == "u_1"
    assert await memory.search_viewer_facts(query="", top_k=5) == []


async def test_list_facts_since_watermark(memory: SimpleMemory) -> None:
    """水位过滤：只返回 created_at_ms 之后的新事实，时间正序。"""
    await memory.add_viewer_fact(platform="bilibili", user_id="u_1", fact_text="旧事实", created_at_ms=1_000)
    await memory.add_viewer_fact(platform="bilibili", user_id="u_1", fact_text="新事实甲", created_at_ms=3_000)
    await memory.add_viewer_fact(platform="bilibili", user_id="u_1", fact_text="新事实乙", created_at_ms=2_000)
    facts = await memory.list_facts_since(platform="bilibili", user_id="u_1", since_ms=1_500)
    assert [f.fact_text for f in facts] == ["新事实乙", "新事实甲"]


async def test_delete_fact(memory: SimpleMemory) -> None:
    """单条删除：命中 True，重复删除 False。"""
    await memory.add_viewer_fact(platform="bilibili", user_id="u_1", fact_text="待删")
    facts = await memory.list_viewer_facts(platform="bilibili", user_id="u_1")
    fact_id = facts[0].fact_id
    assert await memory.delete_viewer_fact(fact_id=fact_id) is True
    assert await memory.delete_viewer_fact(fact_id=fact_id) is False
    assert await memory.list_viewer_facts(platform="bilibili", user_id="u_1") == []


# =============================================================================
# 画像面
# =============================================================================


async def test_profile_upsert_and_get(memory: SimpleMemory) -> None:
    """写入 / 覆盖画像；get 返回文本，无画像返回 None。"""
    assert await memory.get_viewer_profile(platform="bilibili", user_id="u_1") is None
    await memory.upsert_viewer_profile(
        platform="bilibili", user_id="u_1", profile_text="老粉，舰长", last_compressed_at_ms=1_000
    )
    assert await memory.get_viewer_profile(platform="bilibili", user_id="u_1") == "老粉，舰长"
    await memory.upsert_viewer_profile(
        platform="bilibili", user_id="u_1", profile_text="老粉，三年舰长", last_compressed_at_ms=2_000
    )
    row = await memory.get_viewer_profile_with_watermark(platform="bilibili", user_id="u_1")
    assert row is not None
    assert row.profile_text == "老粉，三年舰长"
    assert row.last_compressed_at_ms == 2_000


async def test_profile_upsert_ignores_empty_text(memory: SimpleMemory) -> None:
    """空文本不写（防 LLM 异常输出清空画像）。"""
    await memory.upsert_viewer_profile(
        platform="bilibili", user_id="u_1", profile_text="原画像", last_compressed_at_ms=1
    )
    await memory.upsert_viewer_profile(platform="bilibili", user_id="u_1", profile_text="  ", last_compressed_at_ms=2)
    assert await memory.get_viewer_profile(platform="bilibili", user_id="u_1") == "原画像"


async def test_profile_list_search_and_pagination(memory: SimpleMemory) -> None:
    """列表分页 + 搜索画像文本 / user_id。"""
    for uid in ("u_1", "u_2", "u_3"):
        await memory.upsert_viewer_profile(
            platform="bilibili", user_id=uid, profile_text=f"画像{uid}喜欢Minecraft", last_compressed_at_ms=1
        )
    total, items = await memory.list_viewer_profiles(limit=2, offset=0)
    assert total == 3
    assert len(items) == 2
    total, items = await memory.list_viewer_profiles(search="u_2")
    assert total == 1
    assert items[0].user_id == "u_2"
    total, _ = await memory.list_viewer_profiles(search="Minecraft")
    assert total == 3


async def test_profile_update_text_and_delete(memory: SimpleMemory) -> None:
    """人工纠正画像文本；删除幂等。"""
    await memory.upsert_viewer_profile(
        platform="bilibili", user_id="u_1", profile_text="自动生成的画像", last_compressed_at_ms=1
    )
    assert (
        await memory.update_viewer_profile_text(platform="bilibili", user_id="u_1", profile_text="人工纠正后的画像")
        is True
    )
    assert await memory.get_viewer_profile(platform="bilibili", user_id="u_1") == "人工纠正后的画像"
    assert await memory.update_viewer_profile_text(platform="bilibili", user_id="u_x", profile_text="不存在") is False
    assert await memory.delete_viewer_profile(platform="bilibili", user_id="u_1") is True
    assert await memory.delete_viewer_profile(platform="bilibili", user_id="u_1") is False
    assert await memory.get_viewer_profile(platform="bilibili", user_id="u_1") is None


# =============================================================================
# 画像生成输入（候选清单）
# =============================================================================


async def test_profile_candidates_filter_by_watermark_and_threshold(
    memory: SimpleMemory, viewer_repo: ViewerRepo
) -> None:
    """候选 = 水位后有新事实且互动量达门槛；无画像行视为水位 0。"""
    # 达门槛 + 有新事实 → 候选
    await _seed_viewer(viewer_repo, user_id="u_ok", user_name="达标", interaction_count=3)
    # 不达门槛 → 排除
    await _seed_viewer(viewer_repo, user_id="u_low", user_name="潜水", interaction_count=1)
    # 达门槛但无事实 → 排除
    await _seed_viewer(viewer_repo, user_id="u_empty", user_name="没说过话", interaction_count=9)

    await memory.add_viewer_fact(platform="bilibili", user_id="u_ok", fact_text="喜欢数码产品", created_at_ms=100)
    await memory.add_viewer_fact(platform="bilibili", user_id="u_low", fact_text="想看恐怖游戏", created_at_ms=100)

    candidates = await memory.list_profile_candidates(min_interactions=3)
    keys = {(c.platform, c.user_id) for c in candidates}
    assert ("bilibili", "u_ok") in keys
    assert ("bilibili", "u_low") not in keys
    assert ("bilibili", "u_empty") not in keys


async def test_profile_candidates_exclude_recompressed(memory: SimpleMemory, viewer_repo: ViewerRepo) -> None:
    """水位推进后（无更新事实）不再出现在候选中。"""
    await _seed_viewer(viewer_repo, user_id="u_ok", interaction_count=3)
    await memory.add_viewer_fact(platform="bilibili", user_id="u_ok", fact_text="想学吉他", created_at_ms=100)
    await memory.upsert_viewer_profile(
        platform="bilibili", user_id="u_ok", profile_text="想学吉他", last_compressed_at_ms=500
    )
    assert await memory.list_profile_candidates(min_interactions=3) == []


# =============================================================================
# 工具：query_memory / query_viewer_profile（注册到 ToolRegistry）
# =============================================================================


async def test_query_tool_unbound_returns_failure(memory: SimpleMemory) -> None:
    """未绑定 memory 的 provider 调用返回失败 result。"""
    provider = build_memory_tools()
    inv = ToolInvocation(tool_name="memory_query_memory", arguments={"query": "x"})
    res = await provider.invoke(inv)
    assert res.success is False
    assert "未绑定" in (res.error_message or "")


async def test_query_memory_tool_returns_facts(memory: SimpleMemory) -> None:
    """query_memory 召回事实文本。"""
    await memory.add_viewer_fact(platform="bilibili", user_id="u_1", fact_text="主播推荐的书是《深入理解计算机系统》")
    provider = build_memory_tools(memory=memory)
    registry = ToolRegistry()
    assert registry.register_provider(provider) == 2  # 双工具

    res = await registry.invoke(
        ToolInvocation(tool_name="memory_query_memory", arguments={"query": "深入理解", "top_k": 3})
    )
    assert res.success is True
    assert "深入理解" in res.content


async def test_query_memory_tool_empty_query(memory: SimpleMemory) -> None:
    """空 query 返回占位文本。"""
    provider = build_memory_tools(memory=memory)
    res = await provider.invoke(ToolInvocation(tool_name="memory_query_memory", arguments={"query": ""}))
    assert res.success is True
    assert "空查询" in res.content


async def test_long_fact_survives_storage_and_tool_recall(memory: SimpleMemory) -> None:
    """长事实的尾部仍能检索命中，并完整出现在给模型的工具结果中。"""
    fact = "约定细节" * 300 + "最终要求保留蓝色屋顶"
    assert await memory.add_viewer_fact(platform="bilibili", user_id="long-fact", fact_text=fact)
    rows = await memory.list_viewer_facts(platform="bilibili", user_id="long-fact")
    assert rows[0].fact_text == fact
    result = await build_memory_tools(memory=memory).invoke(
        ToolInvocation(tool_name="memory_query_memory", arguments={"query": "保留蓝色屋顶"})
    )
    assert result.success and fact in result.content


async def test_query_viewer_profile_by_user_id(memory: SimpleMemory) -> None:
    """按 user_id 查画像；无画像给出明确占位。"""
    await memory.upsert_viewer_profile(
        platform="bilibili", user_id="u_1", profile_text="21 级牌子老粉，舰长", last_compressed_at_ms=1
    )
    provider = build_memory_tools(memory=memory)
    res = await provider.invoke(ToolInvocation(tool_name="memory_query_viewer_profile", arguments={"user_id": "u_1"}))
    assert res.success is True
    assert "21 级牌子老粉" in res.content

    res_empty = await provider.invoke(
        ToolInvocation(tool_name="memory_query_viewer_profile", arguments={"user_id": "u_nope"})
    )
    assert res_empty.success is True
    assert "暂无画像" in res_empty.content


async def test_query_viewer_profile_by_nickname(memory: SimpleMemory, viewer_repo: ViewerRepo) -> None:
    """昵称反查：经 ViewerRepo 定位 user_id 再取画像。"""
    await _seed_viewer(viewer_repo, user_id="u_nick", user_name="三楼老王", interaction_count=2)
    await memory.upsert_viewer_profile(
        platform="bilibili", user_id="u_nick", profile_text="元老级观众，什么梗都接得住", last_compressed_at_ms=1
    )
    provider = build_memory_tools(memory=memory, viewer_repo=viewer_repo)
    res = await provider.invoke(
        ToolInvocation(tool_name="memory_query_viewer_profile", arguments={"nickname": "三楼老王"})
    )
    assert res.success is True
    assert "元老级观众" in res.content

    res_miss = await provider.invoke(
        ToolInvocation(tool_name="memory_query_viewer_profile", arguments={"nickname": "不存在的人"})
    )
    assert "未找到昵称" in res_miss.content


async def test_query_viewer_profile_requires_identity(memory: SimpleMemory) -> None:
    """nickname 与 user_id 都缺 → 提示补参。"""
    provider = build_memory_tools(memory=memory)
    res = await provider.invoke(ToolInvocation(tool_name="memory_query_viewer_profile", arguments={}))
    assert res.success is True
    assert "nickname 或 user_id" in res.content
