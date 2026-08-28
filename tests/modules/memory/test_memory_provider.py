"""
MemoryProvider / SimpleMemory 单元测试（Wave 3 / §1.50 + Wave 8 中文召回修复）

覆盖：
- MemoryProvider 接口（Protocol）可被实现/检查
- SimpleMemory（SQLite 关键词召回）：
  - ingest 写入后 recall 能命中
  - get_person_profile 缺记录返回空画像
  - upsert_person_profile 创建 + 合并更新
  - maintain() 空实现不报错
- query_memory 工具：注册入 ToolRegistry 后 invoke 返回文本
- Wave 8 CJK 召回：
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
    MemoryHit,
    MemoryProvider,
    MemoryWriteResult,
    PersonProfile,
    QueryMemoryToolProvider,
    SimpleMemory,
    build_query_memory_tool,
)
from src.modules.memory.simple_memory import _extract_keywords
from src.modules.storage import SQLiteStore
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
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteStore, None]:
    s = SQLiteStore(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
async def memory(store: SQLiteStore) -> AsyncGenerator[SimpleMemory, None]:
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
    required = {"recall", "ingest", "get_person_profile", "maintain"}
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
# PersonProfile：缺记录返回空画像 / upsert
# =============================================================================


async def test_get_person_profile_missing_returns_empty(memory: SimpleMemory) -> None:
    """缺记录返回空 PersonProfile（person_id 透传，其他字段空）。"""
    profile = await memory.get_person_profile("absent_user")
    assert isinstance(profile, PersonProfile)
    assert profile.person_id == "absent_user"
    assert profile.tags == []
    assert profile.summary == ""
    assert profile.display_name == ""


async def test_upsert_person_profile_creates_and_updates(memory: SimpleMemory) -> None:
    """upsert 行为：首次创建；二次合并更新。"""
    # 创建
    profile = await memory.upsert_person_profile(
        person_id="u_1",
        display_name="alice",
        tags=["技术向"],
        summary="经常问技术问题",
    )
    assert profile.person_id == "u_1"
    assert profile.display_name == "alice"
    assert "技术向" in profile.tags
    assert profile.summary == "经常问技术问题"

    # 二次合并：display_name 不传，保留旧值；tags 增加新值；summary 更新
    profile2 = await memory.upsert_person_profile(
        person_id="u_1",
        tags=["技术向", "老观众"],  # 含一个已存在标签
        summary="长期技术向观众",
    )
    assert profile2.display_name == "alice", "display_name 未传应保留"
    assert "技术向" in profile2.tags
    assert "老观众" in profile2.tags
    # 去重
    assert len([t for t in profile2.tags if t == "技术向"]) == 1
    assert profile2.summary == "长期技术向观众"

    # 读出确认
    fetched = await memory.get_person_profile("u_1")
    assert fetched.display_name == "alice"
    assert "老观众" in fetched.tags


async def test_upsert_empty_person_id_rejected(memory: SimpleMemory) -> None:
    """空 person_id 抛 ValueError。"""
    with pytest.raises(ValueError):
        await memory.upsert_person_profile(person_id="")


# =============================================================================
# SQL 注入防护（参数化查询）：恶意输入应原样落库、无异常、无破坏
# =============================================================================


async def test_upsert_malicious_person_id_with_quotes_and_semicolons(
    memory: SimpleMemory,
) -> None:
    """person_id 含单引号 + 分号 + DROP 语句片段 —— 必须原样落库，不得被执行。"""
    evil_id = "u_1'; DROP TABLE _memory_profiles; --"
    await memory.upsert_person_profile(
        person_id=evil_id,
        display_name="x",
        tags=["t"],
        summary="s",
    )

    # 表未被破坏：其他行能正常读写
    await memory.upsert_person_profile(person_id="u_other", display_name="ok")
    fetched_other = await memory.get_person_profile("u_other")
    assert fetched_other.display_name == "ok"

    fetched = await memory.get_person_profile(evil_id)
    assert fetched.person_id == evil_id
    assert fetched.display_name == "x"


async def test_upsert_apostrophe_in_display_name(memory: SimpleMemory) -> None:
    """display_name 含 O'Brien 类撇号 —— 必须原样落库。"""
    pid = "u_apostrophe"
    name = "O'Brien the 2nd"
    summary = "He's a fan."

    await memory.upsert_person_profile(
        person_id=pid,
        display_name=name,
        tags=["a", "b'c"],
        summary=summary,
    )

    fetched = await memory.get_person_profile(pid)
    assert fetched.person_id == pid
    assert fetched.display_name == name, "撇号必须逐字节保留"
    assert "b'c" in fetched.tags, "标签中的撇号必须保留"
    assert fetched.summary == summary


async def test_upsert_multiline_summary_with_double_quotes(
    memory: SimpleMemory,
) -> None:
    """summary 含多行文本 + 双引号 —— 必须原样落库。"""
    pid = "u_multiline"
    summary = (
        '第一行 "带双引号"\n'
        "第二行 with 'single quotes'\n"
        "第三行 \\ 反斜杠 & 符号 <html>\n"
        "第四行 '); DROP TABLE _memory_profiles; --"
    )
    tags = ["tag'1", 'tag"2', "tag\\with\\backslash"]

    await memory.upsert_person_profile(
        person_id=pid,
        display_name='Name "with" quotes',
        tags=tags,
        summary=summary,
    )

    fetched = await memory.get_person_profile(pid)
    assert fetched.person_id == pid
    assert fetched.display_name == 'Name "with" quotes'
    # 用集合等价而非有序 ==：去重逻辑不保证保留原顺序
    assert set(fetched.tags) == {"tag'1", 'tag"2', "tag\\with\\backslash"}
    assert fetched.summary == summary


async def test_upsert_malicious_round_trip_byte_identical(
    memory: SimpleMemory,
) -> None:
    """复合恶意输入：所有字段同时含危险字符，二次 upsert 后内容仍逐字节一致。"""
    pid = "evil'; --"
    name = "O'\"Brien\\"
    summary = '"; DROP TABLE _memory_facts; --\nnewline\rtab\there'

    await memory.upsert_person_profile(
        person_id=pid,
        display_name=name,
        tags=["evil';tag", 'evil"tag', "evil\\backslash"],
        summary=summary,
    )

    # 二次 upsert 仅改 display_name，确认 ON CONFLICT 不破坏其余字段
    await memory.upsert_person_profile(person_id=pid, display_name=name)

    fetched = await memory.get_person_profile(pid)
    assert fetched.person_id == pid
    assert fetched.display_name == name
    assert fetched.summary == summary
    assert set(fetched.tags) == {"evil';tag", 'evil"tag', "evil\\backslash"}


# =============================================================================
# maintain() 占位不报错
# =============================================================================


async def test_maintain_no_op_does_not_error(memory: SimpleMemory) -> None:
    """maintain() 不应抛异常（占位实现）。"""
    await memory.maintain()
    # 多次调用稳定
    await memory.maintain()


# =============================================================================
# query_memory 工具（注册到 ToolRegistry）
# =============================================================================


async def test_query_tool_not_bound_returns_failure(memory: SimpleMemory) -> None:
    """未绑定 memory 的 query_memory provider 调用应返回失败 result。"""
    provider = build_query_memory_tool()  # 未绑定 memory
    inv = ToolInvocation(tool_name="query_memory", arguments={"query": "x"})
    res = await provider.invoke(inv)
    assert res.success is False
    assert "未绑定" in res.error_message or "binding" in res.error_message.lower()


async def test_query_tool_registered_with_memory(memory: SimpleMemory) -> None:
    """绑定 memory 后调 query_memory 工具返回召回文本。"""
    await memory.ingest("主播推荐了一本《深入理解计算机系统》", source="seed")

    provider = build_query_memory_tool()
    provider.memory = memory

    inv = ToolInvocation(tool_name="query_memory", arguments={"query": "深入理解", "top_k": 3})
    res = await provider.invoke(inv)
    assert res.success is True
    assert "深入理解" in res.content
    assert "（无匹配" not in res.content


async def test_query_tool_empty_query_handled(memory: SimpleMemory) -> None:
    """空 query 返回明确占位文本。"""
    provider = build_query_memory_tool()
    provider.memory = memory
    inv = ToolInvocation(tool_name="query_memory", arguments={"query": ""})
    res = await provider.invoke(inv)
    assert res.success is True
    assert "空查询" in res.content


async def test_query_tool_via_tool_registry(memory: SimpleMemory) -> None:
    """query_memory provider 注册到 ToolRegistry 后能正常 invoke。"""
    await memory.ingest("Minecraft 中怎么合成下界合金？", source="seed")

    provider = QueryMemoryToolProvider(memory=memory)
    registry = ToolRegistry()
    registered = registry.register_provider(provider)
    assert registered == 1

    # 已知工具
    res = await registry.invoke(ToolInvocation(tool_name="query_memory", arguments={"query": "合金"}))
    assert res.success is True
    assert "合金" in res.content


# =============================================================================
# 时间字段 *_ms 规范（复检：memory 数据 timestamp_ms 是毫秒 int）
# =============================================================================


async def test_recall_hit_timestamp_is_millisecond(memory: SimpleMemory) -> None:
    """MemoryHit.timestamp_ms 是 int 毫秒（§1.44 / §1.53 9d）。"""
    ts = 1_726_000_000_123  # 12 位（毫秒）
    res = await memory.ingest("时间戳测试", timestamp_ms=ts)
    assert res.accepted
    hits = await memory.recall("时间戳", top_k=1)
    assert len(hits) == 1
    assert isinstance(hits[0].timestamp_ms, int)
    assert hits[0].timestamp_ms == ts


# Wave 8 / 中文召回修复（CJK-aware _extract_keywords）


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
