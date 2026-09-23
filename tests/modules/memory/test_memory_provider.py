"""
MemoryProvider / SimpleMemory 单元测试

覆盖：
- MemoryProvider 接口（Protocol）可被实现/检查
- SimpleMemory（SQLite 关键词召回）：
  - ingest 写入后 recall 能命中
- SimpleMemory 管理面（WebUI 记忆管理页消费）：
  - list_facts 搜索 / 排序白名单 / 分页 / total 全计数
  - update_fact 部分更新语义（None 不动 / tags 覆盖与清空 / 空文本拒绝）
  - delete_fact 幂等（重复删除 False）
  - stats 总数 / 来源计数 / 最新写入
- query_memory 工具：注册入 ToolRegistry 后 invoke 返回文本
- CJK 召回（CJK-aware ``_extract_keywords``）：
  - 中文短句 query 能召回已 ingest 的中文事实（主场景：弹幕直播间）
  - CJK 2-gram 滑动窗口对长 query 起效
  - 混合中英文 query 中 ASCII 词 + CJK 段都被正确抽取
- ``_extract_keywords`` 模块级纯函数：单元测试覆盖核心分桶规则
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.memory import (
    MemoryProvider,
    SimpleMemory,
    build_query_memory_tool,
)
from src.modules.memory.simple_memory import _extract_keywords
from src.modules.storage.database import SQLiteDatabase
from src.modules.time_utils import now_ms
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
async def memory(store: SQLiteDatabase) -> AsyncGenerator[SimpleMemory, None]:
    mem = SimpleMemory(store)
    await mem.initialize()
    yield mem


# =============================================================================
# MemoryProvider 协议（Protocol 接口契约）
# =============================================================================


def test_memory_provider_protocol_recognizes_simple_memory(memory: SimpleMemory) -> None:
    """SimpleMemory 应被识别为 MemoryProvider 协议的实现（runtime_checkable）。"""
    assert isinstance(memory, MemoryProvider), "SimpleMemory 必须实现 MemoryProvider 协议"


def test_memory_provider_required_methods() -> None:
    """MemoryProvider 协议要求的方法列表。"""
    required = {"recall", "ingest"}
    assert required.issubset(set(dir(MemoryProvider)))


# =============================================================================
# SimpleMemory 关键词召回
# =============================================================================


async def test_ingest_then_recall_keyword_hit(memory: SimpleMemory) -> None:
    """写入关键词后能召回（关键词匹配）。"""
    res = await memory.ingest("矿场资源点位", source="seed", importance=10)
    assert res.accepted is True
    assert res.memory_id > 0

    hits = await memory.recall("矿场", top_k=5)
    assert len(hits) >= 1
    assert any("矿场" in h.text for h in hits)


async def test_recall_empty_query_returns_empty(memory: SimpleMemory) -> None:
    """空查询不应爆错。"""
    hits = await memory.recall("", top_k=5)
    assert hits == []
    hits = await memory.recall("   ", top_k=5)
    assert hits == []


async def test_recall_topk_limits_results(memory: SimpleMemory) -> None:
    """top_k 限制返回条数。"""
    for i in range(8):
        await memory.ingest(f"笔记条目 {i}: 关键词minecraft", source=f"test{i}", importance=i)
    hits = await memory.recall("minecraft", top_k=3)
    assert len(hits) == 3


async def test_ingest_empty_text_rejected(memory: SimpleMemory) -> None:
    """空文本被拒绝（不入库）。"""
    res = await memory.ingest("")
    assert res.accepted is False
    res = await memory.ingest("   ")
    assert res.accepted is False


async def test_ingest_timestamp_ms_preserved(memory: SimpleMemory) -> None:
    """timestamp_ms 显式传入时被使用。"""
    ts = now_ms() - 100_000  # 100 秒前
    res = await memory.ingest("过去发生的事件", timestamp_ms=ts)
    assert res.accepted is True

    hits = await memory.recall("过去", top_k=5)
    assert len(hits) == 1
    assert hits[0].timestamp_ms == ts


async def test_ingest_tags_stringified(memory: SimpleMemory) -> None:
    """tags 列表被序列化为字符串存储。"""
    res = await memory.ingest(
        "带有标签",
        tags=["drama", "important"],
    )
    assert res.accepted is True


# =============================================================================
# query_memory 工具（注册到 ToolRegistry）
# =============================================================================


async def test_query_tool_not_bound_returns_failure(memory: SimpleMemory) -> None:
    """未绑定 memory 的 query_memory provider 调用应返回失败 result。"""
    provider = build_query_memory_tool()  # 未绑定 memory
    inv = ToolInvocation(tool_name="memory_query_memory", arguments={"query": "x"})
    res = await provider.invoke(inv)
    assert res.success is False
    assert "未绑定" in res.error_message or "binding" in res.error_message.lower()


async def test_query_tool_registered_with_memory(memory: SimpleMemory) -> None:
    """绑定 memory 后调 query_memory 工具返回召回文本。"""
    await memory.ingest("主播推荐了一本《深入理解计算机系统》", source="seed")

    provider = build_query_memory_tool(memory=memory)

    inv = ToolInvocation(tool_name="memory_query_memory", arguments={"query": "深入理解", "top_k": 3})
    res = await provider.invoke(inv)
    assert res.success is True
    assert "深入理解" in res.content
    assert "（无匹配" not in res.content


async def test_query_tool_empty_query_handled(memory: SimpleMemory) -> None:
    """空 query 返回明确占位文本。"""
    provider = build_query_memory_tool(memory=memory)
    inv = ToolInvocation(tool_name="memory_query_memory", arguments={"query": ""})
    res = await provider.invoke(inv)
    assert res.success is True
    assert "空查询" in res.content


async def test_query_tool_via_tool_registry(memory: SimpleMemory) -> None:
    """query_memory provider 注册到 ToolRegistry 后能正常 invoke（注册名带 memory_ 前缀）。"""
    await memory.ingest("Minecraft 中怎么合成下界合金？", source="seed")

    provider = build_query_memory_tool(memory=memory)
    registry = ToolRegistry()
    registered = registry.register_provider(provider)
    assert registered == 1

    # 注册名 = memory_query_memory
    res = await registry.invoke(ToolInvocation(tool_name="memory_query_memory", arguments={"query": "合金"}))
    assert res.success is True
    assert "合金" in res.content


# =============================================================================
# 时间字段 *_ms 规范（复检：memory 数据 timestamp_ms 是毫秒 int）
# =============================================================================


async def test_recall_hit_timestamp_is_millisecond(memory: SimpleMemory) -> None:
    """MemoryHit.timestamp_ms 是 int 毫秒。"""
    ts = 1_726_000_000_123  # 12 位（毫秒）
    res = await memory.ingest("时间戳测试", timestamp_ms=ts)
    assert res.accepted
    hits = await memory.recall("时间戳", top_k=1)
    assert len(hits) == 1
    assert isinstance(hits[0].timestamp_ms, int)
    assert hits[0].timestamp_ms == ts


# 中文召回（CJK-aware _extract_keywords）


class TestExtractKeywords:
    """``_extract_keywords`` 纯函数单元测试——覆盖三段处理规则 + 边界。"""

    def test_ascii_word_length_ge_2_kept(self) -> None:
        """纯 ASCII 英文/数字词（长度 ≥ 2）保留。"""
        assert _extract_keywords("minecraft") == ["minecraft"]
        assert _extract_keywords("AI2024 update") == ["AI2024", "update"]

    def test_ascii_single_char_dropped(self) -> None:
        """纯 ASCII 单字符不保留（噪声 token）。"""
        assert _extract_keywords("a") == []
        assert _extract_keywords("I") == []

    def test_cjk_short_segment_kept_whole(self) -> None:
        """CJK 连续段长度 2-6 → 整段保留。"""
        assert _extract_keywords("弹幕") == ["弹幕"]
        assert _extract_keywords("弹幕互动") == ["弹幕互动"]
        assert _extract_keywords("主播推荐了一") == ["主播推荐了一"]  # 6 chars 边界

    def test_cjk_long_segment_uses_2gram_boundary_at_7(self) -> None:
        """长度恰好 7（>6 边界外）的 CJK 段触发 2-gram。"""
        assert _extract_keywords("主播推荐了一本") == [
            "主播",
            "播推",
            "推荐",
            "荐了",
            "了一",
            "一本",
        ]

    def test_cjk_short_segment_boundary_at_6_chars(self) -> None:
        """长度恰好 6 的 CJK 段仍整段保留（边界含）。"""
        assert _extract_keywords("主播推荐了一") == ["主播推荐了一"]

    def test_cjk_long_segment_uses_2gram_sliding_window(self) -> None:
        """CJK 连续段长度 > 6 → 2-gram 滑动窗口。"""
        result = _extract_keywords("弹幕互动有什么")
        assert result == ["弹幕", "幕互", "互动", "动有", "有什", "什么"]

    def test_cjk_2gram_truncated_by_max_keywords(self) -> None:
        """长 CJK 段被 ``max_keywords=8`` 截断（12-char → 11 grams → 取前 8）。"""
        result = _extract_keywords("一二三四五六七八九十十一")
        assert len(result) == 8
        assert result == ["一二", "二三", "三四", "四五", "五六", "六七", "七八", "八九"]

    def test_max_keywords_custom_value(self) -> None:
        """``max_keywords`` 显式传 3 也工作。"""
        result = _extract_keywords("一二三四五六七八九十十一十二", max_keywords=3)
        assert result == ["一二", "二三", "三四"]

    def test_dedup_preserves_first_occurrence_order(self) -> None:
        """去重保留首次出现顺序。"""
        result = _extract_keywords("弹幕 弹幕互动")
        assert result[0] == "弹幕"
        assert result.count("弹幕") == 1

    def test_mixed_cjk_and_ascii(self) -> None:
        """混合 token 中 ASCII 词 + CJK 段各自处理。"""
        result = _extract_keywords("Minecraft 怎么合成")
        assert "Minecraft" in result
        # "怎么合成" 4 chars < 6 → 整段保留（不会切分为 "怎么" + "合成"）
        assert "怎么合成" in result

    def test_punctuation_split_preserves_cjk_segments(self) -> None:
        """标点切分不影响 CJK 段（"弹幕、互动、SC" → ["弹幕", "互动", "SC"]）。"""
        result = _extract_keywords("弹幕、互动、SC！")
        assert "弹幕" in result
        assert "互动" in result
        assert "SC" in result

    def test_empty_or_whitespace_returns_empty(self) -> None:
        """空查询 / 纯空白 / None → 返回空列表（recall 走"无关键词短路"分支）。"""
        assert _extract_keywords("") == []
        assert _extract_keywords("   ") == []
        assert _extract_keywords("\n\t  ") == []
        assert _extract_keywords(None) == []  # type: ignore[arg-type]


async def test_recall_cjk_short_query_hits_cjk_fact(memory: SimpleMemory) -> None:
    """主场景：先 ingest 含"弹幕互动"的中文事实，再用部分关键词"弹幕互动"召回。"""
    res = await memory.ingest("弹幕互动很有趣，今天观众很多", source="seed", importance=10)
    assert res.accepted

    hits = await memory.recall("弹幕互动", top_k=5)
    assert len(hits) >= 1
    assert any("弹幕" in h.text for h in hits)
    assert any("互动" in h.text for h in hits)


async def test_recall_cjk_long_query_hits_via_2gram(memory: SimpleMemory) -> None:
    """长 CJK query（>6 字符）通过 2-gram 召回（"弹幕互动" 整词命中 → LIKE '%弹幕%' 命中）。"""
    await memory.ingest("弹幕互动很有趣", source="seed")
    await memory.ingest("今天没有观众", source="seed")

    hits = await memory.recall("弹幕互动有什么好的", top_k=5)
    assert len(hits) >= 1
    assert any("弹幕互动很有趣" in h.text for h in hits)


async def test_recall_cjk_punctuated_query(memory: SimpleMemory) -> None:
    """含标点的中文 query（模拟弹幕语境"主播推荐了，超级喜欢"）仍能命中。"""
    await memory.ingest("主播推荐了一本《深入理解计算机系统》", source="seed", importance=10)

    hits = await memory.recall("主播推荐了，超级喜欢", top_k=5)
    assert len(hits) >= 1
    assert any("深入理解" in h.text for h in hits)


async def test_recall_mixed_cjk_ascii_query(memory: SimpleMemory) -> None:
    """混合 query（中文 + 英文）能同时利用 ASCII 词与 CJK 段。"""
    await memory.ingest("Minecraft 里面合成下界合金很麻烦", source="seed")

    hits = await memory.recall("Minecraft 怎么合成", top_k=5)
    assert len(hits) >= 1
    assert any("Minecraft" in h.text for h in hits)


async def test_recall_cjk_returns_empty_when_no_overlap(memory: SimpleMemory) -> None:
    """CJK query 与数据库内容无交集 → 空结果（不是零命中就崩）。"""
    await memory.ingest("弹幕互动很有趣", source="seed")
    hits = await memory.recall("游戏攻略通关秘籍", top_k=5)
    assert hits == []


async def test_recall_cjk_topk_limits_results(memory: SimpleMemory) -> None:
    """CJK query + top_k 限制生效。"""
    for i in range(5):
        await memory.ingest(f"弹幕互动 笔记{i}", source=f"test{i}")
    hits = await memory.recall("弹幕互动", top_k=2)
    assert len(hits) == 2


# =============================================================================
# SimpleMemory 管理面（WebUI 记忆管理页消费：列表 / 更新 / 删除 / 统计）
# =============================================================================


async def _seed_facts(memory: SimpleMemory, count: int = 5) -> None:
    """造数：交替来源与重要度，便于排序 / 搜索断言。"""
    for i in range(count):
        await memory.ingest(
            f"事实条目{i} 关键词kw{i}",
            source="seed" if i % 2 == 0 else "webui",
            importance=i,
            tags=[f"tag{i}"],
        )


async def test_list_facts_total_and_pagination(memory: SimpleMemory) -> None:
    """total 为全计数，limit/offset 切页。"""
    await _seed_facts(memory, 5)
    total, page1 = await memory.list_facts(limit=2, offset=0)
    total2, page2 = await memory.list_facts(limit=2, offset=2)
    assert total == 5
    assert total2 == 5
    assert len(page1) == 2
    assert len(page2) == 2
    # 默认按 timestamp_ms 倒序：后写入的在前面（id 递增）
    assert page1[0].memory_id > page1[1].memory_id
    assert page2[0].memory_id < page1[-1].memory_id


async def test_list_facts_search_matches_text_source_tags(memory: SimpleMemory) -> None:
    """search 单关键词对 text / source / tags 三列 LIKE。"""
    await memory.ingest("主播喜欢的游戏是 Minecraft", source="seed")
    await memory.ingest("另一条", source="minecraft")
    await memory.ingest("第三条", source="seed", tags=["minecraft"])
    await memory.ingest("无关条目", source="seed")

    total, facts = await memory.list_facts(search="minecraft")
    assert total == 3
    assert {f.memory_id for f in facts} >= {1, 2, 3}


async def test_list_facts_order_by_importance(memory: SimpleMemory) -> None:
    """order_by=importance 按重要度倒序。"""
    await _seed_facts(memory, 5)
    _, facts = await memory.list_facts(order_by="importance", limit=3)
    importances = [f.importance for f in facts]
    assert importances == sorted(importances, reverse=True)
    assert importances[0] == 4


async def test_list_facts_order_whitelist_falls_back(memory: SimpleMemory) -> None:
    """白名单外的 order_by 回落 timestamp_ms（不抛错、不注入）。"""
    await _seed_facts(memory, 3)
    _, facts = await memory.list_facts(order_by="1; DROP TABLE _memory_facts")
    assert len(facts) == 3
    assert await memory._store.table_exists("_memory_facts") is True
    assert [f.memory_id for f in facts] == sorted((f.memory_id for f in facts), reverse=True)


async def test_list_facts_limit_clamped(memory: SimpleMemory) -> None:
    """limit 收敛到 1..200、offset 非负（防御异常入参）。"""
    await _seed_facts(memory, 3)
    _, facts = await memory.list_facts(limit=999)
    assert len(facts) == 3
    _, facts = await memory.list_facts(limit=0)
    assert len(facts) == 1
    total, _ = await memory.list_facts(offset=-5)
    assert total == 3


async def test_update_fact_partial_fields(memory: SimpleMemory) -> None:
    """只更新给定字段，未提及字段保持不变。"""
    res = await memory.ingest("原始文本", source="seed", importance=3, tags=["旧"])
    fact_id = res.memory_id

    assert await memory.update_fact(fact_id, importance=9) is True
    _, facts = await memory.list_facts(search="原始文本")
    assert facts[0].importance == 9
    assert facts[0].text == "原始文本"
    assert facts[0].tags == "旧"
    assert facts[0].source == "seed"


async def test_update_fact_tags_semantics(memory: SimpleMemory) -> None:
    """tags 列表覆盖、空列表清空、字符串原样覆盖。"""
    res = await memory.ingest("标签语义", tags=["a", "b"])
    fact_id = res.memory_id

    await memory.update_fact(fact_id, tags=["x", "y"])
    _, facts = await memory.list_facts(search="标签语义")
    assert facts[0].tags == "x,y"

    await memory.update_fact(fact_id, tags=[])
    _, facts = await memory.list_facts(search="标签语义")
    assert facts[0].tags == ""

    await memory.update_fact(fact_id, tags="单串")
    _, facts = await memory.list_facts(search="标签语义")
    assert facts[0].tags == "单串"


async def test_update_fact_empty_text_rejected(memory: SimpleMemory) -> None:
    """空白文本更新被拒绝（返回 False，原文本保留）。"""
    res = await memory.ingest("保留原文")
    assert await memory.update_fact(res.memory_id, text="   ") is False
    _, facts = await memory.list_facts(search="保留原文")
    assert facts[0].text == "保留原文"


async def test_update_fact_no_fields_returns_false(memory: SimpleMemory) -> None:
    """无任何字段给出 → False（无事可做）。"""
    res = await memory.ingest("无字段更新")
    assert await memory.update_fact(res.memory_id) is False


async def test_update_delete_missing_id_returns_false(memory: SimpleMemory) -> None:
    """id 不存在时 update / delete 都返回 False（幂等安全）。"""
    assert await memory.update_fact(424242, text="改") is False
    assert await memory.delete_fact(424242) is False


async def test_delete_fact_removes_row(memory: SimpleMemory) -> None:
    """删除后行消失，重复删除返回 False。"""
    res = await memory.ingest("待删除条目")
    fact_id = res.memory_id
    assert await memory.delete_fact(fact_id) is True
    total, _ = await memory.list_facts(search="待删除条目")
    assert total == 0
    assert await memory.delete_fact(fact_id) is False


async def test_stats_shape(memory: SimpleMemory) -> None:
    """统计：总数 / 来源计数降序 / 最新写入时刻；空库 latest_ms=0。"""
    empty = await memory.stats()
    assert empty.total_facts == 0
    assert empty.sources == []
    assert empty.latest_ms == 0

    # 计数刻意不打平：避免 GROUP BY 平序时行序不稳定
    for i in range(3):
        await memory.ingest(f"seed 来源条目{i}", source="seed")
    await memory.ingest("webui 来源条目", source="webui")
    stats = await memory.stats()
    assert stats.total_facts == 4
    assert stats.sources == [("seed", 3), ("webui", 1)]
    assert stats.latest_ms > 0
